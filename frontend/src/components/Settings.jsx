import React, { useState, useEffect, useContext, useMemo } from "react";
import { Link } from "react-router-dom";

import { Line, Rect } from "./Skeleton";

import axios from "axios";

import "../styles/Settings.css";

import "../styles/ProgressBar.css";

import { GlobalContext } from "../main";
import { normalizeVisualTheme, VISUAL_THEME_OPTIONS } from "../theme";
import ModelJobsPanel from "./ModelJobsPanel";
import { normalizeToolDisplayMode } from "../utils/toolDisplayModes";

import { registerPush, unregisterPush } from "../utils/push";
import { filterAvailableModelsForField } from "../utils/modelFiltering";
import {
  buildModelGroups,
  DEFAULT_API_MODELS,
  formatLocalRuntimeLabel,
  isLocalRuntimeEntry,
  LOCAL_RUNTIME_ENTRIES,
  normalizeModelId,
  SUGGESTED_LOCAL_MODELS,
  isGptOssModel,
} from "../utils/modelUtils";

const FLOAT_SETTING_FIELDS = new Set([
  "gpu_memory_fraction",
  "gpu_memory_limit_gb",
  "cpu_offload_fraction",
  "cpu_offload_limit_gb",
  "request_timeout",
  "stream_idle_timeout",
  "rag_chat_min_similarity",
  "sae_threads_signal_blend",
]);

const INT_SETTING_FIELDS = new Set([
  "context_length",
  "gpu_memory_margin_mb",
  "cpu_thread_count",
  "local_provider_port",
  "local_provider_default_context_length",
]);

const MANAGED_LOCAL_PROVIDERS = new Set(["lmstudio", "ollama"]);

const SETTINGS_SECTIONS = [
  {
    id: "connections",
    label: "Connections",
    description: "API secrets, endpoints, and knowledge base access.",
    searchText: [
      "connections",
      "access",
      "api key",
      "hf token",
      "endpoint",
      "external api url",
      "mcp server",
      "llm server",
      "weaviate",
      "knowledge base",
      "server url",
    ].join(" "),
  },
  {
    id: "runtime",
    label: "Runtime",
    description: "Mode, provider, device, and request behavior.",
    searchText: [
      "runtime",
      "mode",
      "provider",
      "local runtime",
      "lm studio",
      "ollama",
      "api model",
      "inference device",
      "timeout",
      "request timeout",
      "stream idle timeout",
      "token",
      "context length",
    ].join(" "),
  },
  {
    id: "models",
    label: "Models",
    description: "Language, speech, vision, and retrieval defaults.",
    searchText: [
      "models",
      "downloads",
      "language model",
      "register local model",
      "stt",
      "tts",
      "voice",
      "live streaming",
      "live transcript",
      "realtime",
      "camera",
      "desktop capture",
      "screen share",
      "vision",
      "rag",
      "embedding",
      "clip",
      "retrieval",
      "sae",
      "steering",
    ].join(" "),
  },
  {
    id: "performance",
    label: "Performance",
    description: "Context, GPU/CPU budgets, storage folders, and approvals.",
    searchText: [
      "performance",
      "storage",
      "context",
      "kv cache",
      "ram swap",
      "gpu",
      "cpu",
      "flash attention",
      "models folder",
      "conversations folder",
      "approval",
      "advanced local inference",
    ].join(" "),
  },
  {
    id: "workspace",
    label: "Workspace",
    description: "Notifications, tool display, and the tool browser.",
    searchText: [
      "workspace",
      "appearance",
      "theme",
      "spring",
      "cappucino",
      "notifications",
      "push",
      "tool approval",
      "tool review",
      "tool notifications",
      "tool display",
      "tool browser",
      "tools",
      "tool links",
      "connected tools",
      "custom tools",
      "mcp tool source",
      "agent console",
      "catalog",
    ].join(" "),
  },
  {
    id: "sharing",
    label: "Sharing",
    description: "Trusted-device sync and private live transport.",
    searchText: [
      "sharing",
      "sync",
      "instance sync",
      "pairing",
      "trusted device",
      "private transport",
      "streaming",
      "live voice",
      "realtime",
      "tailnet",
      "vpn",
    ].join(" "),
  },
  {
    id: "output",
    label: "Output",
    description: "Export defaults and prompt customization.",
    searchText: [
      "output",
      "export",
      "conversation export",
      "system prompt",
      "custom instructions",
      "prompt",
      "default channels",
      "tool export",
    ].join(" "),
  },
];

const ACTION_HISTORY_RETENTION_OPTIONS = [
  { value: 0, label: "Off" },
  { value: 1, label: "1 day" },
  { value: 3, label: "3 days" },
  { value: 7, label: "1 week" },
  { value: 14, label: "2 weeks" },
  { value: 30, label: "1 month" },
];

const CAPTURE_RETENTION_OPTIONS = [
  { value: 1, label: "1 day" },
  { value: 3, label: "3 days" },
  { value: 7, label: "1 week" },
  { value: 14, label: "2 weeks" },
  { value: 30, label: "1 month" },
];

const CAPTURE_SENSITIVITY_OPTIONS = [
  { value: "mundane", label: "Mundane" },
  { value: "public", label: "Public" },
  { value: "personal", label: "Personal" },
  { value: "protected", label: "Protected" },
  { value: "secret", label: "Secret" },
];

const DEFAULT_WORKFLOW_CATALOG = {
  workflows: [
    {
      id: "default",
      label: "Default",
      description: "Balanced reasoning with normal tool access and moderate latency.",
      preferred_continue: "mini_execution",
    },
    {
      id: "architect_planner",
      label: "Architect / Planner",
      description: "Higher-reasoning planning workflow that prefers decomposition and explicit handoff.",
      preferred_continue: "mini_execution",
    },
    {
      id: "mini_execution",
      label: "Mini Execution",
      description: "Short, low-latency execution bursts for in-between tool steps and recursive continue loops.",
      preferred_continue: "mini_execution",
    },
  ],
  modules: [
    {
      id: "computer_use",
      label: "Computer Use",
      description: "Browser and desktop observation plus direct UI actions.",
      status: "live",
    },
    {
      id: "camera_capture",
      label: "Camera Capture",
      description: "Still-image capture from a connected camera via the client.",
      status: "experimental",
    },
    {
      id: "memory_promotion",
      label: "Memory Promotion",
      description: "Promote transient captures into durable attachments and later memory workflows.",
      status: "live",
    },
    {
      id: "host_shell",
      label: "Host Shell",
      description: "Approval-gated shell, patch, and host mutation tools.",
      status: "live",
    },
  ],
  addons: [],
  addons_root: "data/modules/addons",
};

const Settings = () => {

  const [loading, setLoading] = useState(true);

  const [saving, setSaving] = useState(false);

  const { state, setState } = useContext(GlobalContext);

  // Service status indicators (duplicated from top bar, plus MCP/Backend)

  const [svcApi, setSvcApi] = useState("loading"); // online | offline | loading

  const [svcBackend, setSvcBackend] = useState("loading"); // online | offline | loading

  const [svcWs, setSvcWs] = useState(state.wsStatus || "offline");

  const [svcMcpUrl, setSvcMcpUrl] = useState(null);

  const [svcMcpReachable, setSvcMcpReachable] = useState(null); // true|false|null

  const [svcMcpProvider, setSvcMcpProvider] = useState('unknown');



  const [svcCelery, setSvcCelery] = useState("loading");

  const [svcCeleryNote, setSvcCeleryNote] = useState("");

  const [ragStatus, setRagStatus] = useState(null);

  const [ragState, setRagState] = useState("loading");

  const [celeryView, setCeleryView] = useState("active"); // active | scheduled | reserved | all

  const [celeryTasks, setCeleryTasks] = useState([]);

  const [celeryLoading, setCeleryLoading] = useState(false);

  const [celeryError, setCeleryError] = useState("");

  const [celeryAuto, setCeleryAuto] = useState(false);

  const [statusAuto, setStatusAuto] = useState(false);

  const [purgeQueue, setPurgeQueue] = useState("celery");

  const [purgeTerminate, setPurgeTerminate] = useState(true);

  const [showFailures, setShowFailures] = useState(false);

  const [failures, setFailures] = useState([]);

  const [failuresLoading, setFailuresLoading] = useState(false);

  const [failuresError, setFailuresError] = useState("");



  const classifyRagState = (data) => {

    if (!data || typeof data !== "object") return "loading";

    if (data.error) return "offline";

    const backend = (data.backend || "").toLowerCase();

    if (backend === "chroma") {

      if (data.exists === false) return "offline";

      if (data.writable === false) return "degraded";

      if (data.documents === null && data.exists) return "degraded";

      if (data.size_bytes === null || typeof data.size_bytes === "undefined") return "degraded";

      if (data.files === null || typeof data.files === "undefined") return "degraded";

      return "online";

    }

    if (backend === "weaviate") {

      return data.url ? "online" : "degraded";

    }

    if (backend === "in-memory" || backend === "memory") {

      return "degraded";

    }

    return backend ? "degraded" : "offline";

  };



  const formatBytes = (value) => {

    if (typeof value !== "number" || Number.isNaN(value) || value < 0) return null;

    if (value === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB", "TB"];

    let num = value;

    let idx = 0;

    while (num >= 1024 && idx < units.length - 1) {

      num /= 1024;

      idx += 1;

    }

    const rounded = num >= 10 || idx === 0 ? Math.round(num) : Math.round(num * 10) / 10;

    return `${rounded} ${units[idx]}`;

  };



  const formatIsoDatetime = (value) => {

    if (!value) return null;

    try {

      const date = new Date(value);

      if (Number.isNaN(date.getTime())) return null;

      return date.toLocaleString();

    } catch (err) {

      return null;

    }

  };

  const formatClockTime = (value) => {

    if (value == null || value === "") return null;

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) return null;

    return date.toLocaleTimeString([], {

      hour: "2-digit",

      minute: "2-digit",

      second: "2-digit",

    });

  };



  const formatRelativeTime = (value) => {

    if (value == null || value === "") return null;

    const diff = Date.now() - value;

    if (!Number.isFinite(diff)) return null;

    if (diff < 0) return "in future";

    const seconds = Math.floor(diff / 1000);

    if (seconds <= 1) return "just now";

    if (seconds < 60) return `${seconds}s ago`;

    const minutes = Math.floor(seconds / 60);

    if (minutes < 60) return `${minutes}m ago`;

    const hours = Math.floor(minutes / 60);

    if (hours < 24) return `${hours}h ago`;

    const days = Math.floor(hours / 24);

    return `${days}d ago`;

  };

  const normalizeExportFormat = (value) => {
    const raw = (value || "").toString().trim().toLowerCase();
    if (raw === "markdown") return "md";
    if (raw === "txt") return "text";
    if (raw === "md" || raw === "json" || raw === "text") return raw;
    return "md";
  };



  const normalizeStatus = (value) => {

    if (typeof value === 'string' && value.trim()) return value.trim().toLowerCase();

    if (value === true) return 'online';

    if (value === false) return 'offline';

    return 'unknown';

  };



  const badgeTone = (value) => {

    if (value === 'online') return 'online';

    if (value === 'loading') return 'loading';

    if (value === 'degraded') return 'loading';

    return 'offline';

  };



  const renderStatusBadge = (value) => {

    const normalized = normalizeStatus(value);

    return (

      <span className={`status-badge status-badge--${badgeTone(normalized)}`}>

        {normalized}

      </span>

    );

  };

  const renderToolStatusBadge = (value) => {
    const normalized = String(value || "live").trim().toLowerCase() || "live";
    const tone =
      normalized === "live"
        ? "online"
        : ["stub", "experimental", "legacy", "planned"].includes(normalized)
          ? "loading"
          : "offline";
    return (
      <span className={`status-badge status-badge--${tone}`}>
        {normalized}
      </span>
    );
  };



  const applyCelerySnapshot = (snapshot) => {

    if (!snapshot || typeof snapshot !== "object") return false;

    const workers = Array.isArray(snapshot.workers) ? snapshot.workers : [];

    const workerCount = workers.length;

    const online = !!snapshot.online;

    const timeout = !!snapshot.timeout;

    let statusValue = "offline";

    let note = "";

    if (online && workerCount > 0) {

      statusValue = "online";

      note = workerCount === 1 ? "1 worker" : `${workerCount} workers`;

    } else if (timeout) {

      statusValue = "degraded";

      note = "timeout contacting workers";

    } else if (online && workerCount === 0) {

      statusValue = "degraded";

      note = "ready (no workers responding)";

    } else if (workerCount > 0) {

      statusValue = "degraded";

      note = workerCount === 1 ? "1 worker (no pong)" : `${workerCount} workers (no pong)`;

    } else if (snapshot.error) {

      statusValue = "offline";

      note = String(snapshot.error);

    } else {

      statusValue = "offline";

      note = "unreachable";

    }

    setSvcCelery(statusValue);

    setSvcCeleryNote(note);

    return true;

  };



  const refreshStatus = async () => {

    setSvcApi("loading");

    setSvcCelery("loading");

    setSvcCeleryNote("");

    setRagState("loading");

    setRagStatus(null);

    let apiOk = false;

    try {

      const r = await axios.get("/api/health");

      apiOk = r?.data?.status === "healthy";

    } catch {

      apiOk = false;

    }

    if (apiOk) {

      const provider = (state.apiProviderStatus || "").toLowerCase();

      const degraded = provider && !["online", "unknown", "bypassed"].includes(provider);

      setSvcApi(degraded ? "degraded" : "online");

    } else {

      setSvcApi("offline");

    }

    try {

      const r2 = await axios.get("/health");

      setSvcBackend(r2?.data?.status === "healthy" ? "online" : "offline");

    } catch {

      setSvcBackend(apiOk ? "online" : "offline");

    }

    try {

      const r3 = await axios.get("/api/mcp/status");

      setSvcMcpUrl(r3?.data?.url || null);

      const reach = typeof r3?.data?.reachable === "boolean" ? r3.data.reachable : null;

      setSvcMcpReachable(reach);

      setSvcMcpProvider((r3?.data?.provider || 'unknown').toLowerCase());

    } catch {

      setSvcMcpUrl(null);

      setSvcMcpReachable(null);

      setSvcMcpProvider('unknown');

      setSvcMcpReachable(null);

    }

    let celeryApplied = false;

    try {

      const ragRes = await axios.get("/api/rag/status");

      const ragData = ragRes && typeof ragRes.data === "object" ? ragRes.data : null;

      if (ragData) {

        setRagStatus(ragData);

        setRagState(classifyRagState(ragData));

        if (ragData.celery) {

          celeryApplied = applyCelerySnapshot(ragData.celery);

        }

      } else {

        setRagStatus({ error: "unreachable" });

        setRagState("offline");

      }

    } catch (err) {

      setRagStatus({ error: "unreachable" });

      setRagState("offline");

    }

    if (!celeryApplied) {

      try {

        const r4 = await axios.get("/api/celery/status");

        const data = r4?.data || {};

        if (!applyCelerySnapshot(data)) {

          setSvcCelery("offline");

          setSvcCeleryNote("unreachable");

        }

      } catch {

        setSvcCelery("offline");

        setSvcCeleryNote("unreachable");

      }

    }

    setSvcWs(state.wsStatus || "offline");

  };



  const refreshFailures = async () => {

    setFailuresLoading(true);

    setFailuresError("");

    try {

      const r = await axios.get('/api/celery/failures', { params: { limit: 50 } });

      setFailures(Array.isArray(r?.data?.failures) ? r.data.failures : []);

    } catch (e) {

      setFailures([]);

      setFailuresError('Failed to load failures');

    } finally {

      setFailuresLoading(false);

    }

  };

  const [settings, setSettings] = useState({

    api_key: "",
    api_key_set: false,
    api_key_preview: "",
    hf_token: "",
    hf_token_set: false,
    hf_token_preview: "",

    api_url: "",

    local_url: "",

    mode: "api",

    model: "",

    dynamic_model: "",

    dynamic_port: "",

    conv_folder: "./data/conversations",

    // leave empty until server settings load, to avoid writing to '/models'

    models_folder: "",

    approvalLevel: state.approvalLevel,

    transformer_model: state.transformerModel,
    local_provider: "lmstudio",
    local_provider_mode: "local-managed",
    local_provider_base_url: "",
    local_provider_host: "127.0.0.1",
    local_provider_port: 1234,
    lmstudio_path: "",
    local_provider_api_token: "",
    local_provider_api_token_set: false,
    local_provider_api_token_preview: "",
    local_provider_auto_start: true,
    local_provider_preferred_model: "",
    local_provider_default_context_length: null,
    local_provider_show_server_logs: true,
    local_provider_enable_cors: false,
    local_provider_allow_lan: false,

  static_model: state.staticModel,

  harmony_format: state.harmonyFormat,

  server_url: state.serverUrl,

    stt_model: state.sttModel,

    tts_model: state.ttsModel,

    voice_model: state.voiceModel,
    stream_backend: "api",
    realtime_model: "gpt-realtime",
    realtime_voice: "alloy",
    realtime_base_url: "https://api.openai.com/v1/realtime/client_secrets",
    realtime_connect_url: "https://api.openai.com/v1/realtime/calls",

  vision_model: state.visionModel,

  context_length: state.maxContextLength,

  kv_cache: state.kvCache,

  ram_swap: state.ramSwap,
  request_timeout: null,
  stream_idle_timeout: null,
  device_map_strategy: "auto",
    gpu_memory_fraction: 0.9,
    gpu_memory_margin_mb: 512,
    gpu_memory_limit_gb: 0,
    cpu_offload_fraction: 0.85,
    cpu_offload_limit_gb: 0,
    flash_attention: false,
    attention_implementation: "",
    kv_cache_implementation: "",
    kv_cache_quant_backend: "",
    kv_cache_dtype: "",
    kv_cache_device: "",
    model_dtype: "",
    cpu_thread_count: 0,
    low_cpu_mem_usage: true,

    // RAG / Weaviate
    rag_embedding_model: "local:all-MiniLM-L6-v2",
    rag_clip_model: "ViT-B-32",
    rag_chat_min_similarity: 0.3,
    sae_threads_signal_mode: "hybrid",
    sae_threads_signal_blend: 0.7,
    sae_model_combo: "openai/gpt-oss-20b :: future SAE pack",
    sae_embeddings_fallback: true,
    sae_steering_enabled: false,
    sae_steering_layer: 12,
    sae_steering_features: "123:+0.8,91:-0.4",
    sae_steering_token_positions: "last",
    sae_steering_dry_run: true,
    sae_live_inspect_console: false,
    weaviate_url: "",
    weaviate_auto_start: false,

    devices: [],

    default_device: null,

    inference_device: null,

    cuda_diagnostics: null,

  });

  const [message, setMessage] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);

  const [serverPlatform, setServerPlatform] = useState(null);

  const [pathHints, setPathHints] = useState({ models: "", conversations: "" });

  // when false, rely on server default search dirs; do not send models_folder on save

  const [useCustomModelsFolder, setUseCustomModelsFolder] = useState(false);

  // when false, rely on backend default conversations folder; do not send conv_folder on save

  const [useCustomConvFolder, setUseCustomConvFolder] = useState(false);

  const [pushAvailable, setPushAvailable] = useState(false);

  const [pushEnabled, setPushEnabled] = useState(false);

  const [notifyMinutes, setNotifyMinutes] = useState(5);
  const [toolResolutionNotifications, setToolResolutionNotifications] = useState(true);
  const [actionHistoryRetentionDays, setActionHistoryRetentionDays] = useState(7);
  const [actionHistorySaving, setActionHistorySaving] = useState(false);
  const [actionHistoryMessage, setActionHistoryMessage] = useState("");
  const [notificationPrefMessage, setNotificationPrefMessage] = useState("");
  const [captureRetentionDays, setCaptureRetentionDays] = useState(
    Math.max(1, Number(state.captureRetentionDays) || 7),
  );
  const [captureDefaultSensitivity, setCaptureDefaultSensitivity] = useState(
    state.captureDefaultSensitivity || "personal",
  );
  const [captureAllowModelRawImageAccess, setCaptureAllowModelRawImageAccess] = useState(
    state.captureAllowModelRawImageAccess !== false,
  );
  const [captureAllowSummaryFallback, setCaptureAllowSummaryFallback] = useState(
    state.captureAllowSummaryFallback !== false,
  );
  const [defaultWorkflow, setDefaultWorkflow] = useState(state.workflowProfile || "default");
  const [enabledWorkflowModules, setEnabledWorkflowModules] = useState(
    Array.isArray(state.enabledWorkflowModules) ? state.enabledWorkflowModules : [],
  );
  const [workflowCatalog, setWorkflowCatalog] = useState(DEFAULT_WORKFLOW_CATALOG);
  const [workflowCatalogLoading, setWorkflowCatalogLoading] = useState(false);
  const [captureWorkflowSaving, setCaptureWorkflowSaving] = useState(false);
  const [captureWorkflowMessage, setCaptureWorkflowMessage] = useState("");

  const [exportDefaults, setExportDefaults] = useState({
    format: "md",
    includeChat: true,
    includeThoughts: true,
    includeTools: true,
  });
  const [systemPromptBase, setSystemPromptBase] = useState("");
  const [systemPromptCustom, setSystemPromptCustom] = useState("");
  const [systemPromptSaving, setSystemPromptSaving] = useState(false);
  const [systemPromptMessage, setSystemPromptMessage] = useState("");
  const [exportSaving, setExportSaving] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [exportAllBusy, setExportAllBusy] = useState(false);
  const [syncRemoteUrl, setSyncRemoteUrl] = useState("");
  const [syncPreview, setSyncPreview] = useState(null);
  const [syncSelections, setSyncSelections] = useState({});
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncActionBusy, setSyncActionBusy] = useState("");
  const [syncMessage, setSyncMessage] = useState("");
  const [syncLinkToSourceDevice, setSyncLinkToSourceDevice] = useState(false);
  const [syncSourceNamespace, setSyncSourceNamespace] = useState("");
  const [syncDefaultsSaving, setSyncDefaultsSaving] = useState(false);
  const [syncDialogOpen, setSyncDialogOpen] = useState(false);
  const [settingsSearch, setSettingsSearch] = useState("");
  const [toolCatalog, setToolCatalog] = useState([]);
  const [toolLimits, setToolLimits] = useState(null);
  const [toolCatalogLoading, setToolCatalogLoading] = useState(false);
  const [toolCatalogError, setToolCatalogError] = useState("");
  const [toolCatalogFilter, setToolCatalogFilter] = useState("");

  const [availableModels, setAvailableModels] = useState([]);
  const [providerRuntime, setProviderRuntime] = useState(null);
  const [providerRuntimeLoading, setProviderRuntimeLoading] = useState(false);
  const [providerRuntimeError, setProviderRuntimeError] = useState("");
  const [providerModelOptions, setProviderModelOptions] = useState([]);
  const [providerActionBusy, setProviderActionBusy] = useState("");
  const [providerActionMessage, setProviderActionMessage] = useState("");
  const [includeCacheUnfiltered, setIncludeCacheUnfiltered] = useState(false);
  const [registeredLocalModels, setRegisteredLocalModels] = useState([]);
  const [registerModelAlias, setRegisterModelAlias] = useState("");
  const [registerModelPath, setRegisterModelPath] = useState("");
  const [registerModelType, setRegisterModelType] = useState("transformer");
  const [registerModelBusy, setRegisterModelBusy] = useState(false);
  const [registerModelMessage, setRegisterModelMessage] = useState("");
  const availableModelSet = useMemo(() => {
    const set = new Set();
    (availableModels || []).forEach((model) => {
      if (typeof model === "string" && model.trim()) {
        set.add(model.trim());
      }
    });
    return set;
  }, [availableModels]);

  const registeredModelAliasSet = useMemo(() => {
    const set = new Set();
    (registeredLocalModels || []).forEach((entry) => {
      const alias = typeof entry?.alias === "string" ? entry.alias.trim() : "";
      if (alias) set.add(alias);
    });
    return set;
  }, [registeredLocalModels]);

  const apiModelsAvailable = Array.isArray(state.apiModels) ? state.apiModels : [];
  const apiModelsAvailableSet = useMemo(
    () => new Set(apiModelsAvailable),
    [apiModelsAvailable],
  );
  const apiModelGroups = useMemo(
    () =>
      buildModelGroups({
        defaults: DEFAULT_API_MODELS,
        discovered: apiModelsAvailable,
        current: settings.model,
      }),
    [apiModelsAvailable, settings.model],
  );
  const filteredToolCatalog = useMemo(() => {
    const query = String(toolCatalogFilter || "")
      .trim()
      .toLowerCase();
    if (!query) return toolCatalog;
    return (toolCatalog || []).filter((entry) => {
      if (!entry || typeof entry !== "object") return false;
      const haystack = [
        entry.display_name,
        entry.id,
        entry.summary,
        entry.description,
        entry.category,
        entry.status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [toolCatalog, toolCatalogFilter]);
  const toolStatusSummary = useMemo(() => {
    const summary = { live: 0, stub: 0, legacy: 0, other: 0 };
    (toolCatalog || []).forEach((entry) => {
      const status = String(entry?.status || "")
        .trim()
        .toLowerCase();
      if (status === "live") summary.live += 1;
      else if (status === "stub") summary.stub += 1;
      else if (status === "legacy") summary.legacy += 1;
      else summary.other += 1;
    });
    return summary;
  }, [toolCatalog]);
  const toolSourceCards = useMemo(() => {
    const provider = String(svcMcpProvider || "unknown").trim().toLowerCase() || "unknown";
    const hasMcpConfig = Boolean(svcMcpUrl) || provider !== "unknown";
    const mcpState =
      svcMcpReachable === true ? "online" : hasMcpConfig ? "degraded" : "offline";
    return [
      {
        id: "builtin",
        label: "Built-in tools",
        badge: "live",
        description: `${toolCatalog.length} cataloged tools are currently visible in Settings.`,
        details: [
          `${toolStatusSummary.live} live`,
          `${toolStatusSummary.stub} stub`,
          `${toolStatusSummary.legacy} legacy`,
        ],
      },
      {
        id: "mcp",
        label: "Connected source",
        badge: mcpState,
        description:
          svcMcpReachable === true
            ? "MCP bridge is reachable from Settings."
            : hasMcpConfig
              ? "MCP bridge is configured but not currently reachable."
              : "No external MCP tool source is configured yet.",
        details: [
          `provider: ${provider}`,
          svcMcpUrl ? svcMcpUrl : "No MCP URL reported",
        ],
      },
      {
        id: "custom",
        label: "Custom tools",
        badge: "planned",
        description:
          "Settings does not yet create saved HTTP or MCP-backed custom tools.",
        details: [
          "Read-only for now",
          "Planned follow-up: custom HTTP/MCP tool management",
        ],
      },
    ];
  }, [
    svcMcpProvider,
    svcMcpReachable,
    svcMcpUrl,
    toolCatalog.length,
    toolStatusSummary.legacy,
    toolStatusSummary.live,
    toolStatusSummary.stub,
  ]);
  const settingsSearchTerms = useMemo(
    () =>
      String(settingsSearch || "")
        .trim()
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean),
    [settingsSearch],
  );
  const visibleSettingsSections = useMemo(() => {
    if (!settingsSearchTerms.length) return SETTINGS_SECTIONS;
    return SETTINGS_SECTIONS.filter((section) =>
      settingsSearchTerms.every((term) => section.searchText.includes(term)),
    );
  }, [settingsSearchTerms]);
  const visibleSettingsSectionIds = useMemo(
    () => new Set(visibleSettingsSections.map((section) => section.id)),
    [visibleSettingsSections],
  );
  const showSettingsSection = (sectionId) =>
    !settingsSearchTerms.length || visibleSettingsSectionIds.has(sectionId);

  const suggestedLangModels = Array.from(
    new Set([
      ...(Array.isArray(SUGGESTED_LOCAL_MODELS) ? SUGGESTED_LOCAL_MODELS : []),
      ...(Array.isArray(LOCAL_RUNTIME_ENTRIES) ? LOCAL_RUNTIME_ENTRIES : []),
    ]),
  );

  const suggestedSttModels = ["whisper-large-v3-turbo", "whisper-small"];

  const suggestedTtsModels = ["tts-1", "tts-1-hd", "kokoro", "kitten"];

  const describeModelProvider = (field, value) => {
    const normalizedField = String(field || "").trim().toLowerCase();
    const normalizedValue = String(value || "").trim().toLowerCase();
    if (!normalizedValue) return "";
    if (normalizedField === "tts_model") {
      if (normalizedValue.startsWith("tts-")) return "OpenAI API";
      if (normalizedValue.includes("kitten") || normalizedValue.includes("kokoro")) {
        return "local engine";
      }
    }
    return "";
  };

  const openAiVoiceOptions = ["alloy", "nova", "shimmer", "echo", "fable", "onyx"];
  const realtimeModelOptions = ["gpt-realtime"];
  const realtimeVoiceOptions = [
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "marin",
    "sage",
    "shimmer",
    "verse",
  ];
  const kittenVoiceOptions = [
    "expr-voice-2-f",
    "expr-voice-3-f",
    "expr-voice-4-f",
    "expr-voice-5-f",
    "expr-voice-2-m",
    "expr-voice-3-m",
    "expr-voice-4-m",
    "expr-voice-5-m",
  ];
  const kokoroVoiceOptions = ["af_heart", "af_bella", "af_nova", "bf_emma"];
  const voicePresetOptions = [
    ...openAiVoiceOptions,
    ...kittenVoiceOptions,
    ...kokoroVoiceOptions,
  ];

  const ragEmbeddingPresets = [
    { label: "Hash fallback (local)", value: "simple" },
    { label: "Sentence Transformers · all-MiniLM-L6-v2", value: "local:all-MiniLM-L6-v2" },
    { label: "OpenAI text-embedding-3-large (API stub)", value: "api:text-embedding-3-large" },
  ];

  const ragClipPresets = [
    { label: "OpenCLIP ViT-B-32 (recommended)", value: "ViT-B-32" },
    { label: "OpenCLIP ViT-B-16", value: "ViT-B-16" },
    { label: "OpenCLIP ViT-L-14", value: "ViT-L-14" },
  ];

  const suggestedVisionModels = [

    "clip-vit-base-patch32",

    "paligemma2-3b-pt-224",

    "paligemma2-28b-pt-896",

    "pixtral-12b-2409",

  ];

  const primaryGpu = useMemo(() => {
    if (!Array.isArray(settings.devices)) return null;
    return settings.devices.find((device) => device && device.type === "cuda");
  }, [settings.devices]);

  const gpuTotalGb =
    primaryGpu && typeof primaryGpu.total_memory_gb === "number"
      ? primaryGpu.total_memory_gb
      : null;

  const gpuBudgetGb = useMemo(() => {
    if (!gpuTotalGb) return null;
    const fraction =
      typeof settings.gpu_memory_fraction === "number"
        ? settings.gpu_memory_fraction
        : 0;
    return Number((gpuTotalGb * fraction).toFixed(2));
  }, [gpuTotalGb, settings.gpu_memory_fraction]);

  const gpuFractionPercent = useMemo(() => {
    const fraction =
      typeof settings.gpu_memory_fraction === "number"
        ? settings.gpu_memory_fraction
        : 0;
    return Math.round(fraction * 100);
  }, [settings.gpu_memory_fraction]);

  const RESPONSES_SUFFIX = "/responses";

  const COMPLETIONS_SUFFIX = "/chat/completions";

  const endpointStatus = useMemo(() => {

    const raw = (settings.api_url || "").trim();

    if (!raw) {

      return {

        level: "ok",

        message: "Defaulting to the Responses API endpoint.",

      };

    }

    const stripped = raw.split(/[?#]/)[0].replace(/\/+$/, "");

    const normalized = stripped.toLowerCase();

    if (normalized.endsWith(RESPONSES_SUFFIX)) {

      return {

        level: "ok",

        message: "Responses API endpoint detected.",

      };

    }

    if (normalized.endsWith(COMPLETIONS_SUFFIX)) {

      return {

        level: "warn",

        message:

          "Chat Completions endpoint is deprecated. Switch to /responses for full feature support.",

      };

    }

    return {

      level: "warn",

      message:

        "Endpoint does not end with /responses. Confirm your provider supports the Responses API.",

    };

  }, [settings.api_url]);

  const endpointWarning = endpointStatus.level === "warn";

  const modelFields = [

    "transformer_model",

    "stt_model",

    "tts_model",

    "vision_model",

  ];

  const [modelInfos, setModelInfos] = useState({});

  const [modelAvailable, setModelAvailable] = useState({});

  const [modelLocalSizes, setModelLocalSizes] = useState({});

  const [modelVerified, setModelVerified] = useState({});

  const [modelExpectedBytes, setModelExpectedBytes] = useState({});

  const [modelDownloadable, setModelDownloadable] = useState({});

  const [downloadingModel, setDownloadingModel] = useState({});

  const registeredModelOptionsByField = useMemo(() => {
    const registrationMatchesField = (field, modelType) => {
      const normalized = String(modelType || "other").toLowerCase();
      if (field === "transformer_model") {
        return normalized === "transformer" || normalized === "other";
      }
      if (field === "stt_model") return normalized === "stt";
      if (field === "tts_model") return normalized === "tts";
      if (field === "vision_model") return normalized === "vision";
      if (field === "voice_model") return normalized === "voice";
      return true;
    };
    const mapped = {};
    modelFields.forEach((field) => {
      mapped[field] = [];
    });
    (registeredLocalModels || []).forEach((entry) => {
      const alias = typeof entry?.alias === "string" ? entry.alias.trim() : "";
      if (!alias) return;
      if (entry?.exists === false) return;
      modelFields.forEach((field) => {
        if (registrationMatchesField(field, entry?.model_type)) {
          mapped[field].push(alias);
        }
      });
    });
    Object.keys(mapped).forEach((field) => {
      mapped[field] = Array.from(new Set(mapped[field]));
    });
    return mapped;
  }, [modelFields, registeredLocalModels]);



  // model downloads/progress are handled globally in DownloadTray

  const [vramEstimate, setVramEstimate] = useState(0);

  // Track initial baseline for dirty-checking and initialization status

  const [initialComparable, setInitialComparable] = useState(null);

  const [initialized, setInitialized] = useState(false);



  // Families/variants mapping for two-step selection

  const MODEL_VARIANTS = {

    transformer: {

      "gpt-oss": { "20b": "gpt-oss-20b", "120b": "gpt-oss-120b" },

    },

    vision: {

      paligemma: {

        "2.7b": "paligemma-2.7b",

        "28b-448": "paligemma2-28b-mix-448",

      },

      clip: { "vit-b-32": "clip-vit-base-patch32" },

    },

  };
  // Legacy variant picker is currently disabled; keep placeholder for future re-enable.
  const _renderVariantModelField = (_label, _category, _field) => null;

  const NON_DOWNLOADABLE = new Set(["nova"]);

  const [variants, setVariants] = useState({

    transformer_family: "gpt-oss",

    transformer_variant: "20b",

    vision_family: "clip",

    vision_variant: "vit-b-32",

  });



  useEffect(() => {

    refreshStatus();

  }, []);



  // Keep ws indicator in sync with global

  useEffect(() => {

    setSvcWs(state.wsStatus || "offline");

  }, [state.wsStatus]);



  // Keep API status in sync when it changes globally

  useEffect(() => {

    if (state.apiStatus) setSvcApi(state.apiStatus);

  }, [state.apiStatus]);



  const renderStatusDot = (status) => {

    const tone =

      status === "online"

        ? "ok"

        : status === "loading" || status === "degraded"

        ? "warn"

        : "err";

    return <span className={`status-dot ${tone}`} aria-hidden="true" />;

  };



  const refreshCeleryTasks = async (view = celeryView) => {

    setCeleryLoading(true);

    setCeleryError("");

    try {

      const r = await axios.get("/api/celery/tasks", { params: { state: view, limit: 50 } });

      setCeleryTasks(Array.isArray(r?.data?.tasks) ? r.data.tasks : []);

    } catch (e) {

      setCeleryTasks([]);

      setCeleryError("Failed to fetch tasks");

    } finally {

      setCeleryLoading(false);

    }

  };



  useEffect(() => {

    if (!celeryAuto) return;

    const id = setInterval(() => refreshCeleryTasks(), 8000);

    return () => clearInterval(id);

  }, [celeryAuto, celeryView]);



  useEffect(() => {

    if (!statusAuto) return;

    const id = setInterval(() => refreshStatus(), 15000);

    return () => clearInterval(id);

  }, [statusAuto]);



  useEffect(() => {

    axios

      .get("/api/settings", { timeout: 10000 })

      .then((response) => {

        const data = response.data;
        const requestTimeoutCandidate =
          data.request_timeout ?? data.llm_request_timeout ?? data.timeout;
        const requestTimeoutSec = Number(requestTimeoutCandidate);
        const normalizedRequestTimeout =
          Number.isFinite(requestTimeoutSec) && requestTimeoutSec > 0 ? requestTimeoutSec : null;
        const streamIdleTimeoutSec = Number(data.stream_idle_timeout);
        const normalizedStreamIdleTimeout =
          Number.isFinite(streamIdleTimeoutSec) && streamIdleTimeoutSec > 0
            ? streamIdleTimeoutSec
            : null;
        const provider = normalizeModelId(data.local_provider) || "lmstudio";
        const providerPortRaw = Number(data.local_provider_port);
        const providerPortFallback = provider === "ollama" ? 11434 : 1234;
        const providerPort =
          Number.isFinite(providerPortRaw) && providerPortRaw > 0
            ? providerPortRaw
            : providerPortFallback;

        const newSettings = {

          api_key: "",
          api_key_set: !!data.api_key_set,
          api_key_preview: data.api_key_preview || "",
          hf_token: "",
          hf_token_set: !!data.hf_token_set,
          hf_token_preview: data.hf_token_preview || "",

          api_url: data.api_url || "",

          local_url: data.local_url || "",

          mode:

            data.mode === "local-small"

              ? "local"

              : data.mode === "local-cloud"

                ? "server"

                : data.mode === "local-static"

                  ? "local"

                  : data.mode === "cloud"

                    ? "server"

                    : data.mode || "api",

          model: data.model || "",

          dynamic_model: data.dynamic_model || "",

          dynamic_port: data.dynamic_port ? String(data.dynamic_port) : "",

          inference_device:
            data.inference_device ||
            (data.default_device && typeof data.default_device === "object"
              ? data.default_device.id || null
              : null),

          conv_folder: data.conv_folder || "./data/conversations",

          // trust server-provided default; don't fall back to '/models'

          models_folder: data.models_folder || "",

          approvalLevel: state.approvalLevel,

          transformer_model: data.transformer_model || "gpt-oss-20b",
          local_provider: provider,
          local_provider_mode: data.local_provider_mode || "local-managed",
          local_provider_base_url: data.local_provider_base_url || "",
          local_provider_host: data.local_provider_host || "127.0.0.1",
          local_provider_port: providerPort,
          lmstudio_path: data.lmstudio_path || "",
          local_provider_api_token: "",
          local_provider_api_token_set: !!data.local_provider_api_token_set,
          local_provider_api_token_preview:
            data.local_provider_api_token_preview || "",
          local_provider_auto_start: data.local_provider_auto_start ?? true,
          local_provider_preferred_model:
            data.local_provider_preferred_model || "",
          local_provider_default_context_length:
            typeof data.local_provider_default_context_length === "number" &&
            data.local_provider_default_context_length > 0
              ? data.local_provider_default_context_length
              : null,
          local_provider_show_server_logs:
            data.local_provider_show_server_logs ?? true,
          local_provider_enable_cors: data.local_provider_enable_cors ?? false,
          local_provider_allow_lan: data.local_provider_allow_lan ?? false,

          static_model: data.static_model || "gpt-4o-mini",

          harmony_format: data.harmony_format ?? false,

          server_url: data.server_url || "",

          stt_model: data.stt_model || "whisper-1",

          tts_model: data.tts_model || "tts-1",

          // Default to a valid OpenAI TTS voice name

          voice_model: data.voice_model || "alloy",
          stream_backend: data.stream_backend || "api",
          realtime_model: data.realtime_model || "gpt-realtime",
          realtime_voice: data.realtime_voice || "alloy",
          realtime_base_url:
            data.realtime_base_url ||
            "https://api.openai.com/v1/realtime/client_secrets",
          realtime_connect_url:
            data.realtime_connect_url ||
            "https://api.openai.com/v1/realtime/calls",

          vision_model: data.vision_model || "google/paligemma2-3b-pt-224",

          request_timeout: normalizedRequestTimeout,

          stream_idle_timeout: normalizedStreamIdleTimeout,

          context_length: data.max_context_length || 2048,

          kv_cache: data.kv_cache ?? true,

          ram_swap: data.ram_swap ?? false,
          device_map_strategy: data.device_map_strategy || "auto",
          gpu_memory_fraction:
            typeof data.gpu_memory_fraction === "number"
              ? data.gpu_memory_fraction
              : 0.9,
          gpu_memory_margin_mb:
            typeof data.gpu_memory_margin_mb === "number"
              ? data.gpu_memory_margin_mb
              : 512,
          gpu_memory_limit_gb:
            typeof data.gpu_memory_limit_gb === "number"
              ? data.gpu_memory_limit_gb
              : 0,
          cpu_offload_fraction:
            typeof data.cpu_offload_fraction === "number"
              ? data.cpu_offload_fraction
              : 0.85,
          cpu_offload_limit_gb:
            typeof data.cpu_offload_limit_gb === "number"
              ? data.cpu_offload_limit_gb
              : 0,
          flash_attention: data.flash_attention ?? false,
          attention_implementation: data.attention_implementation || "",
          kv_cache_implementation: data.kv_cache_implementation || "",
          kv_cache_quant_backend: data.kv_cache_quant_backend || "",
          kv_cache_dtype: data.kv_cache_dtype || "",
          kv_cache_device: data.kv_cache_device || "",
          model_dtype: data.model_dtype || "",
          cpu_thread_count:
            typeof data.cpu_thread_count === "number"
              ? data.cpu_thread_count
              : 0,
          low_cpu_mem_usage: data.low_cpu_mem_usage ?? true,

          devices: Array.isArray(data.devices) ? data.devices : [],

          default_device: data.default_device || null,
          cuda_diagnostics: data.cuda_diagnostics || null,

          weaviate_url: data.weaviate_url || "http://localhost:8080",
          weaviate_auto_start: !!data.weaviate_auto_start,
          rag_embedding_model: data.rag_embedding_model || "local:all-MiniLM-L6-v2",
          rag_clip_model: data.rag_clip_model || "ViT-B-32",
          rag_chat_min_similarity:
            typeof data.rag_chat_min_similarity === "number"
              ? data.rag_chat_min_similarity
              : 0.3,
          sae_threads_signal_mode:
            typeof data.sae_threads_signal_mode === "string" &&
            ["embeddings", "hybrid", "sae"].includes(
              data.sae_threads_signal_mode.toLowerCase(),
            )
              ? data.sae_threads_signal_mode.toLowerCase()
              : "hybrid",
          sae_threads_signal_blend:
            typeof data.sae_threads_signal_blend === "number" &&
            Number.isFinite(data.sae_threads_signal_blend)
              ? Math.min(1, Math.max(0, data.sae_threads_signal_blend))
              : 0.7,
          sae_model_combo:
            typeof data.sae_model_combo === "string" && data.sae_model_combo.trim()
              ? data.sae_model_combo.trim()
              : "openai/gpt-oss-20b :: future SAE pack",
          sae_embeddings_fallback:
            typeof data.sae_embeddings_fallback === "boolean"
              ? data.sae_embeddings_fallback
              : true,
          sae_steering_enabled:
            typeof data.sae_steering_enabled === "boolean"
              ? data.sae_steering_enabled
              : false,
          sae_steering_layer:
            typeof data.sae_steering_layer === "number" &&
            Number.isFinite(data.sae_steering_layer)
              ? data.sae_steering_layer
              : 12,
          sae_steering_features:
            typeof data.sae_steering_features === "string"
              ? data.sae_steering_features
              : "123:+0.8,91:-0.4",
          sae_steering_token_positions:
            typeof data.sae_steering_token_positions === "string" &&
            data.sae_steering_token_positions.trim()
              ? data.sae_steering_token_positions.trim()
              : "last",
          sae_steering_dry_run:
            typeof data.sae_steering_dry_run === "boolean"
              ? data.sae_steering_dry_run
              : true,
          sae_live_inspect_console:
            typeof data.sae_live_inspect_console === "boolean"
              ? data.sae_live_inspect_console
              : false,

        };

        setServerPlatform(data.server_platform || null);

        setPathHints({

          models: data.default_models_dir || "",

          conversations: data.default_conv_dir || "",

        });

        // Initialize family/variant from current models if possible

        const inferFV = (category, model) => {

          const table = MODEL_VARIANTS[category] || {};

          for (const [fam, vmap] of Object.entries(table)) {

            for (const [variant, full] of Object.entries(vmap)) {

              if (full === model) return { fam, variant };

            }

          }

          return null;

        };

        const tf = inferFV("transformer", newSettings.transformer_model);

        const vf = inferFV("vision", newSettings.vision_model);

        setVariants((prev) => ({

          ...prev,

          ...(tf

            ? { transformer_family: tf.fam, transformer_variant: tf.variant }

            : {}),

          ...(vf ? { vision_family: vf.fam, vision_variant: vf.variant } : {}),

        }));

        setSettings(newSettings);

        setState((prev) => {
          const next = {
            ...prev,
            devices: newSettings.devices,
            defaultDevice: newSettings.default_device,
            cudaDiagnostics: newSettings.cuda_diagnostics,
            inferenceDevice:
              newSettings.inference_device ??
              prev.inferenceDevice ??
              (newSettings.default_device
                ? newSettings.default_device.id || newSettings.default_device.name
                : null),
          };
          if (typeof newSettings.request_timeout === "number") {
            next.requestTimeoutSec = newSettings.request_timeout;
          }
          if (typeof newSettings.stream_idle_timeout === "number") {
            next.streamIdleTimeoutSec = newSettings.stream_idle_timeout;
          }
          return next;
        });

        // default to not using custom folders; user can opt-in via checkbox

        setUseCustomModelsFolder(false);

        setUseCustomConvFolder(false);

        // If not using custom folder, let backend pick default by omitting path

        fetchAvailableModels(false ? newSettings.models_folder : undefined);
        fetchRegisteredLocalModels();

      })

      .catch((err) => {

        console.error(err);

      })

      .finally(() => {

        setLoading(false);

      });

  }, []);

  const refreshToolCatalog = async () => {
    setToolCatalogLoading(true);
    setToolCatalogError("");
    try {
      const [catalogRes, limitsRes] = await Promise.all([
        axios.get("/api/tools/catalog"),
        axios.get("/api/tools/limits"),
      ]);
      setToolCatalog(Array.isArray(catalogRes?.data?.tools) ? catalogRes.data.tools : []);
      setToolLimits(
        limitsRes?.data && typeof limitsRes.data === "object" ? limitsRes.data : null,
      );
    } catch (err) {
      setToolCatalog([]);
      setToolLimits(null);
      setToolCatalogError("Tool catalog unavailable.");
    } finally {
      setToolCatalogLoading(false);
    }
  };

  useEffect(() => {
    refreshToolCatalog();
  }, []);



  const fetchAvailableModels = (folder, unfiltered = includeCacheUnfiltered) => {
    const params = {};
    if (folder) params.path = folder;
    if (unfiltered) params.include_cache_unfiltered = true;
    const reqParams = Object.keys(params).length > 0 ? { params } : undefined;

    return axios

      .get("/api/transformers/models", reqParams)

      .then((r) => {

        const models = r.data.models || [];

        setAvailableModels(models);

      })

      .catch(() => {

        setAvailableModels([]);

      });

  };

  const refreshProviderRuntime = async (quiet = false) => {
    const providerKey = normalizeModelId(settings.local_provider) || "lmstudio";
    const shouldInspect =
      settings.mode === "local" && MANAGED_LOCAL_PROVIDERS.has(providerKey);
    if (!shouldInspect) {
      setProviderRuntime(null);
      setProviderModelOptions([]);
      setProviderRuntimeError("");
      setProviderRuntimeLoading(false);
      return;
    }
    if (!quiet) {
      setProviderRuntimeLoading(true);
    }
    try {
      const [statusResponse, modelsResponse] = await Promise.all([
        axios.get("/api/llm/provider/status", {
          params: { provider: providerKey, quick: true },
        }),
        axios.get("/api/llm/provider/models", {
          params: { provider: providerKey },
        }),
      ]);
      setProviderRuntime(statusResponse?.data?.runtime || null);
      setProviderModelOptions(
        Array.isArray(modelsResponse?.data?.models) ? modelsResponse.data.models : [],
      );
      setProviderRuntimeError("");
    } catch (err) {
      setProviderRuntime(null);
      setProviderModelOptions([]);
      setProviderRuntimeError(
        err?.response?.data?.detail || "Provider runtime is not reachable right now.",
      );
    } finally {
      if (!quiet) {
        setProviderRuntimeLoading(false);
      }
    }
  };

  const runProviderAction = async (action) => {
    const providerKey = normalizeModelId(settings.local_provider) || "lmstudio";
    if (!providerRuntimeInspectable) {
      return;
    }
    const model =
      (settings.local_provider_preferred_model || "").trim() || undefined;
    const contextLength =
      typeof settings.local_provider_default_context_length === "number" &&
      settings.local_provider_default_context_length > 0
        ? settings.local_provider_default_context_length
        : undefined;
    const actionLabels = {
      start: "Provider start requested.",
      stop: "Provider stop requested.",
      load: model
        ? `Provider load requested for ${model}.`
        : "Provider load requested.",
      unload: "Provider unload requested.",
    };
    setProviderActionBusy(action);
    setProviderActionMessage("");
    try {
      const payload = { provider: providerKey };
      if (model) {
        payload.model = model;
      }
      if (typeof contextLength === "number" && action === "load") {
        payload.context_length = contextLength;
      }
      await axios.post(`/api/llm/provider/${action}`, payload);
      setProviderActionMessage(actionLabels[action] || "Provider action requested.");
      await refreshProviderRuntime(true);
    } catch (err) {
      setProviderActionMessage(
        err?.response?.data?.detail || "Provider action failed.",
      );
    } finally {
      setProviderActionBusy("");
    }
  };



  // ---------------------------

  // Weaviate status / controls

  // ---------------------------

  const [weaviateStatus, setWeaviateStatus] = useState({ url: "", reachable: null });

  const [wvLoading, setWvLoading] = useState(false);

  const [wvStarting, setWvStarting] = useState(false);

  const [wvMessage, setWvMessage] = useState("");

  const refreshWeaviateStatus = async () => {

    setWvLoading(true);

    try {

      const r = await axios.get("/api/weaviate/status", { params: { url: settings.weaviate_url } });

      setWeaviateStatus({ url: r?.data?.url || settings.weaviate_url, reachable: !!r?.data?.reachable });

    } catch {

      setWeaviateStatus({ url: settings.weaviate_url, reachable: false });

    } finally {

      setWvLoading(false);

    }

  };

  useEffect(() => {

    // Load initial status once settings are loaded

    if (!loading) refreshWeaviateStatus();

    // eslint-disable-next-line react-hooks/exhaustive-deps

  }, [loading]);



  const handleWeaviateStart = async () => {

    setWvStarting(true);

    try {

      const resp = await axios.post("/api/weaviate/start", { url: settings.weaviate_url, wait_seconds: 30 });

      const ok = !!(resp && resp.data && resp.data.reachable);

      setWvMessage(

        ok

          ? `Weaviate is running at ${resp.data?.url || settings.weaviate_url}`

          : "Attempted to start Weaviate, but it is not reachable yet."

      );

      // Auto-clear the notice after a short delay

      try { setTimeout(() => setWvMessage(""), 6000); } catch {}

    } catch (e) {

      setWvMessage("Failed to start Weaviate. Check Docker and compose file.");

      try { setTimeout(() => setWvMessage(""), 6000); } catch {}

    } finally {

      setWvStarting(false);

      refreshWeaviateStatus();

    }

  };



  const loadingView = (

    <div className="center-rail" style={{ paddingTop: 16 }}>

      <Line width="30%" />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 12 }}>

        <Rect height={160} />

        <Rect height={160} />

      </div>

      <Line width="50%" />

      <Rect height={220} />

    </div>

  );



  // Simple status section for core services (API, backend, WS, MCP)

  const StatusSection = () => {

    const rawMcpState =
      svcMcpReachable === null
        ? 'loading'
        : svcMcpReachable
          ? 'online'
          : svcMcpUrl
            ? 'degraded'
            : 'offline';
    const mcpNote = rawMcpState === 'degraded'
      ? (svcMcpProvider === 'stub' ? 'stub active' : 'endpoint unreachable')
      : '';
    const mcpState = normalizeStatus(rawMcpState);
    const ragStateNormalized = normalizeStatus(ragState);
    const ragBackendName =
      ragStatus && typeof ragStatus.backend === 'string' && ragStatus.backend
        ? ragStatus.backend
        : 'unknown';
    const ragBackendDisplay = ragBackendName
      .split(/[-_]/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ') || ragBackendName;
    const ragHasStatus = Boolean(ragStatus && !ragStatus.error);
    const ragDocCount =
      ragHasStatus && typeof ragStatus.documents === 'number'
        ? ragStatus.documents
        : null;
    const ragFileCount =
      ragHasStatus && typeof ragStatus.files === 'number' && ragStatus.files >= 0
        ? ragStatus.files
        : null;
    const ragSizeLabel =
      ragHasStatus && typeof ragStatus.size_bytes === 'number' && ragStatus.size_bytes >= 0
        ? formatBytes(ragStatus.size_bytes)
        : null;
    const ragSummaryParts = [];
    if (ragDocCount !== null) ragSummaryParts.push(`${ragDocCount} docs`);
    if (ragFileCount !== null) ragSummaryParts.push(`${ragFileCount} files`);
    if (ragSizeLabel) ragSummaryParts.push(ragSizeLabel);
    const ragSummary = ragSummaryParts.join(' • ');
    const ragNoteTone = ['degraded', 'offline', 'unknown'].includes(ragStateNormalized)
      ? 'warn'
      : '';
    let ragTooltip = '';
    if (ragHasStatus) {
      const segments = [];
      if (ragBackendDisplay) segments.push(ragBackendDisplay);
      if (ragStatus.persist_dir) segments.push(`path: ${ragStatus.persist_dir}`);
      ragTooltip = segments.join(' • ');
      if (!ragTooltip && ragStatus.backend) {
        ragTooltip = ragStatus.backend;
      }
    } else if (ragStatus && ragStatus.persist_dir) {
      ragTooltip = `path: ${ragStatus.persist_dir}`;
    }
    const ragLastUpdated = ragHasStatus ? formatIsoDatetime(ragStatus.last_modified) : null;
    const ragError = ragStatus && ragStatus.error ? String(ragStatus.error) : '';
    const wsLastEventAgo = formatRelativeTime(state.wsLastEventAt);
    const wsLastEventClock = formatClockTime(state.wsLastEventAt);
    const wsLastEventLabel =
      wsLastEventAgo && wsLastEventClock
        ? `${wsLastEventAgo} (${wsLastEventClock})`
        : wsLastEventAgo || wsLastEventClock || null;
    const wsLastErrorAgo = formatRelativeTime(state.wsLastErrorAt);
    const wsLastErrorClock = formatClockTime(state.wsLastErrorAt);
    const wsErrorWhen =
      wsLastErrorAgo && wsLastErrorClock
        ? `${wsLastErrorAgo} (${wsLastErrorClock})`
        : wsLastErrorAgo || wsLastErrorClock || null;
    const wsErrorMessage = state.wsLastError ? String(state.wsLastError) : "";
    const wsErrorDisplay =
      wsErrorMessage && wsErrorMessage.length > 200
        ? `${wsErrorMessage.slice(0, 197)}...`
        : wsErrorMessage;
    return (

      <div className="settings-section">

        <div className="status-header">

          <h2 className="mb-sm" style={{ margin: 0 }}>Service status</h2>

          <div className="inline-flex" style={{ gap: 8 }}>

            <label className="inline-flex" style={{ gap: 6 }} title="Auto-refresh status every ~15s">

              <input

                type="checkbox"

                checked={statusAuto}

                onChange={(e) => setStatusAuto(!!e.target.checked)}

              />

              Auto-refresh

            </label>

            <button type="button" onClick={() => refreshStatus()}>Refresh</button>

          </div>

        </div>

        <div className="status-grid">

          <div className="status-item" title="Primary API router (/api)">

            {renderStatusDot(svcApi)}

            <div>

              <div className="status-label">API</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(svcApi)}

                {state.backendMode === "api" && state.apiProviderStatus && !["online", "bypassed", "unknown"].includes((state.apiProviderStatus || "").toLowerCase()) && (

                  <span className={`status-note ${svcApi === "degraded" ? "warn" : ""}`}>

                    provider: {state.apiProviderStatus}

                  </span>

                )}

              </div>

            </div>

          </div>

          <div className="status-item" title="Backend process (Uvicorn)">

            {renderStatusDot(svcBackend)}

            <div>

              <div className="status-label">Backend</div>

              <div className="status-sub">{renderStatusBadge(svcBackend)}</div>

            </div>

          </div>

          <div className="status-item" title="WebSocket: /api/ws/thoughts">

            {renderStatusDot(normalizeStatus(svcWs))}

            <div>

              <div className="status-label">WebSocket</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(svcWs)}

                {wsLastEventLabel && (

                  <span className="status-note">

                    last event {wsLastEventLabel}

                  </span>

                )}

                {wsErrorDisplay && (

                  <span className="status-note warn">

                    {wsErrorWhen ? `error ${wsErrorWhen}: ${wsErrorDisplay}` : `error: ${wsErrorDisplay}`}

                  </span>

                )}

              </div>

            </div>

          </div>

          <div className="status-item" title={svcMcpUrl ? `MCP at ${svcMcpUrl}` : 'MCP URL not set'}>

            {renderStatusDot(rawMcpState)}

            <div>

              <div className="status-label">MCP</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(mcpState)}

                <span className={`status-note ${rawMcpState === 'degraded' ? 'warn' : ''}`}>

                  {svcMcpUrl ? (

                    <span style={{ wordBreak: 'break-all' }}>{svcMcpUrl}</span>

                  ) : (

                    'not configured'

                  )}

                  {mcpNote && <span style={{ marginLeft: 4 }}>{mcpNote}</span>}

                </span>

              </div>

            </div>

          </div>

          <div className="status-item" title={ragTooltip || 'Vector store persistence'}>

            {renderStatusDot(ragStateNormalized)}

            <div>

              <div className="status-label">Vector store</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(ragStateNormalized)}

                {ragSummary && (

                  <span className={`status-note ${ragNoteTone}`}>

                    {ragSummary}

                  </span>

                )}

                {ragLastUpdated && (

                  <span className="status-note">updated: {ragLastUpdated}</span>

                )}

                {ragError && (

                  <span className="status-note warn">{ragError}</span>

                )}

              </div>

            </div>

          </div>

        </div>

        <div className="celery-panel">

          <div
            className="celery-header"
            style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}
          >

            <div
              className="inline-flex"
              style={{ alignItems: 'center', gap: 8 }}
              title="Celery coordinates background jobs so multiple agents can run in parallel."
            >

              {renderStatusDot(svcCelery)}

              <h3 style={{ margin: 0 }}>Celery tasks</h3>

              {renderStatusBadge(svcCelery)}

              {svcCeleryNote && (

                <span className={`status-note ${svcCelery === 'degraded' ? 'warn' : ''}`}>

                  {svcCeleryNote}

                </span>

              )}

            </div>

            <div className="inline-flex">

              <select

                value={celeryView}

                onChange={(e) => {

                  const v = e.target.value;

                  setCeleryView(v);

                  refreshCeleryTasks(v);

                }}

                title="Task view"

              >

                <option value="active">active</option>

                <option value="scheduled">scheduled</option>

                <option value="reserved">reserved</option>

                <option value="all">all</option>

              </select>

              <label className="inline-flex" style={{ gap: 6 }} title="Auto-refresh every ~8s">

                <input

                  type="checkbox"

                  checked={celeryAuto}

                  onChange={(e) => setCeleryAuto(!!e.target.checked)}

                />

                Auto-refresh

              </label>

              <button type="button" onClick={() => refreshCeleryTasks()} disabled={celeryLoading}>

                {celeryLoading ? 'Refreshing…' : 'Refresh'}

              </button>

            </div>

          </div>

            <div className="inline-flex" style={{ justifyContent: 'space-between', marginTop: 8 }}>

              <div className="inline-flex" style={{ gap: 8 }}>

                <label title="Queue name to purge">Queue</label>

                <input

                  style={{ width: 160 }}

                  type="text"

                  value={purgeQueue}

                  onChange={(e) => setPurgeQueue(e.target.value)}

                  placeholder="celery"

                />

                <label className="inline-flex" style={{ gap: 6 }} title="Terminate running tasks too">

                  <input

                    type="checkbox"

                    checked={purgeTerminate}

                    onChange={(e) => setPurgeTerminate(!!e.target.checked)}

                  />

                  Terminate running

                </label>

                <button

                  type="button"

                  className="icon-btn"

                  onClick={async () => {

                    const needsConfirm = true;

                    if (

                      needsConfirm &&

                      !window.confirm(`Purge queue '${purgeQueue}' and ${

                        purgeTerminate ? 'terminate running' : 'leave running'

                      }?`)

                    )

                      return;

                    try {

                      await axios.post('/api/celery/purge', {

                        queue: purgeQueue || null,

                        terminate_active: purgeTerminate,

                        include_reserved: true,

                        include_scheduled: true,

                        confirm: true,

                      });

                      refreshCeleryTasks();

                    } catch (e) {

                      alert('Purge failed');

                    }

                  }}

                >

                  Purge

                </button>

              </div>

              <div className="inline-flex" style={{ gap: 8 }}>

                <label title="Retry a task by name">Retry name</label>

                <input

                  style={{ width: 260 }}

                  type="text"

                  placeholder="module.task_name"

                  onKeyDown={async (e) => {

                    if (e.key === 'Enter') {

                      const name = e.currentTarget.value.trim();

                      if (!name) return;

                      if (!window.confirm(`Queue task ${name}?`)) return;

                      try {

                        await axios.post('/api/celery/retry', { name, confirm: true });

                        refreshCeleryTasks();

                      } catch {

                        alert('Retry failed');

                      }

                    }

                  }}

                />

              </div>

            </div>

            {celeryError && <div className="alert" role="status">{celeryError}</div>}

            <div className="celery-table-wrap">

              <table className="celery-table">

                <thead>

                  <tr>

                    <th>worker</th>

                    <th>state</th>

                    <th>name</th>

                    <th>id</th>

                    <th>args</th>

                    <th>time</th>

                    <th></th>

                  </tr>

                </thead>

                <tbody>

                  {celeryTasks.length === 0 ? (

                    <tr>

                      <td colSpan={6} style={{ textAlign: 'center', opacity: 0.7 }}>

                        no tasks

                      </td>

                    </tr>

                  ) : (

                    celeryTasks.map((t, i) => {

                      const shortId = (t.id || '').slice(0, 8);

                      const when = t.time_start || t.eta || null;

                      let timeStr = '--';

                      if (when) {

                        try {

                          const dt = new Date(when * 1000);

                          timeStr = dt.toLocaleTimeString();

                        } catch {}

                      }

                      const allowRevoke = ['active', 'reserved', 'scheduled'].includes(

                        String(t.state || '')

                      );

                      return (

                        <tr key={`${t.worker}-${t.id}-${i}`}>

                          <td title={t.worker}>{String(t.worker || '').split('@')[0]}</td>

                          <td>{t.state || ''}</td>

                          <td title={t.name}>{t.name || ''}</td>

                          <td title={t.id}>{shortId}</td>

                          <td title={t.args_hash || ''}>{t.args_hash || ''}</td>

                          <td>{timeStr}</td>

                          <td>

                            <button

                              type="button"

                              className="icon-btn"

                              disabled={!allowRevoke}

                              title={allowRevoke ? 'Revoke task' : 'Cannot revoke'}

                              onClick={async () => {

                                if (!t.id) return;

                                if (!window.confirm(`Revoke task ${t.id}?`)) return;

                                try {

                                  await axios.post(`/api/celery/tasks/${t.id}/revoke`, {

                                    terminate: true,

                                  });

                                  refreshCeleryTasks();

                                } catch (e) {

                                  alert('Failed to revoke task');

                                }

                              }}

                            >

                              ✖

                            </button>

                          </td>

                        </tr>

                      );

                    })

                  )}

                </tbody>

              </table>

            </div>

            <div className="inline-flex" style={{ gap: 8, marginTop: 8 }}>

              <button

                type="button"

                onClick={() => {

                  setShowFailures((v) => !v);

                  if (!showFailures) refreshFailures();

                }}

              >

                {showFailures ? 'Hide failures' : 'Show failures'}

              </button>

              {showFailures && (

                <button type="button" onClick={() => refreshFailures()} disabled={failuresLoading}>

                  {failuresLoading ? 'Refreshing…' : 'Refresh failures'}

                </button>

              )}

            </div>

            {showFailures && (

              <div className="celery-table-wrap" style={{ marginTop: 8 }}>

                {failuresError && (

                  <div className="alert" role="status">

                    {failuresError}

                  </div>

                )}

                <table className="celery-table">

                  <thead>

                    <tr>

                      <th>time</th>

                      <th>name</th>

                      <th>id</th>

                      <th>error</th>

                      <th></th>

                    </tr>

                  </thead>

                  <tbody>

                    {(!failures || failures.length === 0) ? (

                      <tr>

                        <td colSpan={5} style={{ textAlign: 'center', opacity: 0.7 }}>

                          no failures

                        </td>

                      </tr>

                    ) : (

                      failures.map((f, idx) => {

                        let ts = '--';

                        try {

                          ts = new Date((f.ts || 0) * 1000).toLocaleTimeString();

                        } catch {}

                        const shortId = String(f.id || '').slice(0, 8);

                        const msg = f.exc || '';

                        const name = f.name || '';

                        return (

                          <tr key={`${f.id}-${idx}`}>

                            <td>{ts}</td>

                            <td title={name}>{name}</td>

                            <td title={f.id}>{shortId}</td>

                            <td title={msg}>{msg.slice(0, 80)}</td>

                            <td>

                              <button

                                type="button"

                                className="icon-btn"

                                title={name ? 'Retry by name' : 'No name available'}

                                disabled={!name}

                                onClick={async () => {

                                  if (!name) return;

                                  if (!window.confirm(`Queue task ${name}?`)) return;

                                  try {

                                    await axios.post('/api/celery/retry', { name, confirm: true });

                                    refreshCeleryTasks();

                                  } catch {

                                    alert('Retry failed');

                                  }

                                }}

                              >

                                ↻

                              </button>

                            </td>

                          </tr>

                        );

                      })

                    )}

                  </tbody>

                </table>

              </div>

            )}

            <ModelJobsPanel />

          </div>

        </div>

    );

  };



  useEffect(() => {

    // only pass explicit path when using a custom folder

    fetchAvailableModels(useCustomModelsFolder ? settings.models_folder : undefined);

  }, [settings.models_folder, useCustomModelsFolder]);

  useEffect(() => {
    if (loading) return;
    refreshProviderRuntime();
  }, [loading, settings.mode, settings.local_provider]);

  useEffect(() => {
    const providerKey = normalizeModelId(settings.local_provider) || "lmstudio";
    if (!(settings.mode === "local" && MANAGED_LOCAL_PROVIDERS.has(providerKey))) {
      return undefined;
    }
    const id = setInterval(() => {
      refreshProviderRuntime(true);
    }, 20000);
    return () => clearInterval(id);
  }, [settings.mode, settings.local_provider]);



  // Known API-only identifiers; not downloadable locally.

  const API_ONLY = new Set([

    "alloy", // OpenAI TTS voice preset

    "tts-1", // OpenAI TTS model (API)
    "tts-1-hd", // OpenAI TTS model (API)

    "whisper-1", // OpenAI Whisper (API)

    // gemma-3 is downloadable (local) — see backend MODEL_REPOS

  ]);



  useEffect(() => {

    modelFields.forEach((field) => {

      const model = settings[field];

      if (!model) return;

      axios

        .get(`/api/models/info/${model}`)

        .then((r) => {

          setModelInfos((prev) => ({ ...prev, [field]: r.data }));

          const repo = String(r.data?.repo_id || "");

          const dl = !!repo && !repo.startsWith("TODO");

          setModelDownloadable((prev) => ({ ...prev, [field]: dl }));

        })

        .catch(() => {

          setModelInfos((prev) => ({ ...prev, [field]: { size: 0 } }));

          // If info lookup fails, still allow download attempt for known families (Gemma 3)

          const fallbackDl = typeof model === 'string' && model.startsWith('gemma-3');

          setModelDownloadable((prev) => ({ ...prev, [field]: fallbackDl }));

        });

      axios

        .get(`/api/models/exists/${model}`,

          useCustomModelsFolder && settings.models_folder

            ? { params: { path: settings.models_folder } }

            : undefined,

        )

        .then((r) =>

          setModelAvailable((prev) => ({ ...prev, [field]: !!r.data.exists }))

        )

        .catch(() =>

          setModelAvailable((prev) => ({ ...prev, [field]: false }))

        );

      axios

        .get(

          `/api/models/local-size/${model}`,

          useCustomModelsFolder && settings.models_folder

            ? { params: { path: settings.models_folder } }

            : undefined,

        )

        .then((r) =>

          setModelLocalSizes((prev) => ({ ...prev, [field]: r.data.size || 0 }))

        )

        .catch(() =>

          setModelLocalSizes((prev) => ({ ...prev, [field]: 0 }))

        );

      axios

        .get(

          `/api/models/verify/${model}`,

          useCustomModelsFolder && settings.models_folder

            ? { params: { path: settings.models_folder } }

            : undefined,

        )

        .then((r) => {

          setModelVerified((prev) => ({ ...prev, [field]: !!r.data.verified }));

          const exp = r.data?.expected_bytes || 0;

          if (exp > 0)

            setModelExpectedBytes((prev) => ({ ...prev, [field]: exp }));

        })

        .catch(() =>

          setModelVerified((prev) => ({ ...prev, [field]: false }))

        );

    });

  }, [

    settings.transformer_model,

    settings.stt_model,

    settings.tts_model,

    settings.voice_model,

    settings.vision_model,

    settings.models_folder,

  ]);

  useEffect(() => {
    setCaptureRetentionDays(Math.max(1, Number(state.captureRetentionDays) || 7));
    setCaptureDefaultSensitivity(state.captureDefaultSensitivity || "personal");
    setCaptureAllowModelRawImageAccess(state.captureAllowModelRawImageAccess !== false);
    setCaptureAllowSummaryFallback(state.captureAllowSummaryFallback !== false);
    setDefaultWorkflow(state.workflowProfile || "default");
    setEnabledWorkflowModules(
      Array.isArray(state.enabledWorkflowModules) ? state.enabledWorkflowModules : [],
    );
  }, [
    state.captureAllowModelRawImageAccess,
    state.captureAllowSummaryFallback,
    state.captureDefaultSensitivity,
    state.captureRetentionDays,
    state.enabledWorkflowModules,
    state.workflowProfile,
  ]);

  const refreshWorkflowCatalog = async () => {
    setWorkflowCatalogLoading(true);
    try {
      const res = await axios.get("/api/workflows/catalog");
      const payload = res?.data || {};
      setWorkflowCatalog({
        workflows: Array.isArray(payload.workflows)
          ? payload.workflows
          : DEFAULT_WORKFLOW_CATALOG.workflows,
        modules: Array.isArray(payload.modules)
          ? payload.modules
          : DEFAULT_WORKFLOW_CATALOG.modules,
        addons: Array.isArray(payload.addons) ? payload.addons : [],
        addons_root:
          typeof payload.addons_root === "string" && payload.addons_root.trim()
            ? payload.addons_root.trim()
            : DEFAULT_WORKFLOW_CATALOG.addons_root,
      });
    } catch {
      setWorkflowCatalog(DEFAULT_WORKFLOW_CATALOG);
    } finally {
      setWorkflowCatalogLoading(false);
    }
  };

  useEffect(() => {
    refreshWorkflowCatalog();
  }, []);



  useEffect(() => {

    fetch("/api/push/public-key")

      .then((r) => r.json())

      .then((d) => setPushAvailable(!!d.enabled))

      .catch(() => setPushAvailable(false));

    axios

      .get("/api/user-settings")

      .then((r) => {

        const s = r.data || {};

        setPushEnabled(!!s.push_enabled);

        if (typeof s.calendar_notify_minutes === "number")

          setNotifyMinutes(s.calendar_notify_minutes);
        if (typeof s.tool_resolution_notifications === "boolean") {
          setToolResolutionNotifications(s.tool_resolution_notifications);
        }
        if (typeof s.action_history_retention_days === "number") {
          setActionHistoryRetentionDays(s.action_history_retention_days);
        }
        if (typeof s.capture_retention_days === "number") {
          setCaptureRetentionDays(Math.max(1, s.capture_retention_days));
        }
        if (
          typeof s.capture_default_sensitivity === "string" &&
          s.capture_default_sensitivity.trim()
        ) {
          setCaptureDefaultSensitivity(s.capture_default_sensitivity.trim());
        }
        if (typeof s.capture_allow_model_raw_image_access === "boolean") {
          setCaptureAllowModelRawImageAccess(s.capture_allow_model_raw_image_access);
        }
        if (typeof s.capture_allow_summary_fallback === "boolean") {
          setCaptureAllowSummaryFallback(s.capture_allow_summary_fallback);
        }
        if (typeof s.default_workflow === "string" && s.default_workflow.trim()) {
          setDefaultWorkflow(s.default_workflow.trim());
        }
        if (Array.isArray(s.enabled_workflow_modules)) {
          setEnabledWorkflowModules(s.enabled_workflow_modules);
        }
        const nextDefaults = {
          format: normalizeExportFormat(s.export_default_format),
          includeChat:
            typeof s.export_default_include_chat === "boolean"
              ? s.export_default_include_chat
              : true,
          includeThoughts:
            typeof s.export_default_include_thoughts === "boolean"
              ? s.export_default_include_thoughts
              : true,
          includeTools:
            typeof s.export_default_include_tools === "boolean"
              ? s.export_default_include_tools
              : true,
        };
        setExportDefaults(nextDefaults);
        setSystemPromptBase(
          typeof s.system_prompt_base === "string" ? s.system_prompt_base : "",
        );
        setSystemPromptCustom(
          typeof s.system_prompt_custom === "string" ? s.system_prompt_custom : "",
        );
        setSyncLinkToSourceDevice(!!s.sync_link_to_source_device);
        setSyncSourceNamespace(
          typeof s.sync_source_namespace === "string" ? s.sync_source_namespace : "",
        );

      })

      .catch(() => {});

  }, []);



  // Per-model availability is handled in the aggregated checker above.



  useEffect(() => {

    if (state.backendMode !== "local") {

      setVramEstimate(0);

      return;

    }

    axios

      .get("/api/vram-estimate", {

        params: { context_length: settings.context_length },

      })

      .then((r) => setVramEstimate(r.data.estimate_mb || 0))

      .catch(() => setVramEstimate(0));

  }, [settings.context_length, state.backendMode]);



  const handleChange = (e) => {

    const { name, type, value, checked } = e.target;

    let nextValue;
    if (type === "checkbox") {
      nextValue = checked;
    } else if (FLOAT_SETTING_FIELDS.has(name)) {
      const parsed = parseFloat(value);
      nextValue = Number.isFinite(parsed) ? parsed : 0;
    } else if (INT_SETTING_FIELDS.has(name) || type === "range") {
      const parsed = parseInt(value, 10);
      nextValue = Number.isFinite(parsed) ? parsed : 0;
    } else if (type === "number") {
      const parsed = parseInt(value, 10);
      nextValue = Number.isFinite(parsed) ? parsed : 0;
    } else {
      nextValue = value;
    }

    setSettings((prev) => {
      const next = {
        ...prev,
        [name]: nextValue,
      };
      if (name === "transformer_model" && isLocalRuntimeEntry(nextValue)) {
        next.local_provider = normalizeModelId(nextValue);
      }
      if (name === "local_provider") {
        const normalized = normalizeModelId(nextValue);
        if (
          normalized === "ollama" &&
          (prev.local_provider_port === 1234 || !prev.local_provider_port)
        ) {
          next.local_provider_port = 11434;
        } else if (
          normalized === "lmstudio" &&
          (prev.local_provider_port === 11434 || !prev.local_provider_port)
        ) {
          next.local_provider_port = 1234;
        }
      }
      return next;
    });

    if (name === "harmony_format") {

      // Mark as user-overridden so auto-defaulting stops

      setState((prev) => ({ ...prev, harmonyTouched: true }));

    }

  };



  const getServerPathExample = (kind) => {

    if (kind === "models" && pathHints.models) return pathHints.models;

    if (kind === "conversations" && pathHints.conversations) return pathHints.conversations;

    if (serverPlatform === "windows") {

      return kind === "models"

        ? "C:\\path\\to\\float\\data\\models"

        : "C:\\path\\to\\float\\data\\conversations";

    }

    if (serverPlatform === "mac") {

      return kind === "models"
        ? "/path/to/float/data/models"
        : "/path/to/float/data/conversations";

    }

    return kind === "models"
      ? "/path/to/float/data/models"
      : "/path/to/float/data/conversations";

  };



  const promptForServerPath = (field, kind) => {

    const example = getServerPathExample(kind);

    const current = (settings[field] || "").trim();

    const lines = [

      "Browsers cannot open the server's file picker directly.",

      "Type the absolute path on the server you want to use.",

    ];

    if (example) {

      lines.push("Example: " + example);

    }

    const nextValue = window.prompt(lines.join("\n\n"), current || example || "");

    if (nextValue === null) return;

    setSettings((prev) => ({

      ...prev,

      [field]: nextValue.trim(),

    }));

  };



  const handleBrowse = () => {

    promptForServerPath("conv_folder", "conversations");

  };



  const handleModelsBrowse = () => {
 
    promptForServerPath("models_folder", "models");
 
  };

  useEffect(() => {
    const next = {};
    if (settings.model && settings.model !== state.apiModel) {
      next.apiModel = settings.model;
    }
    if (
      settings.transformer_model &&
      settings.transformer_model !== state.localModel
    ) {
      next.localModel = settings.transformer_model;
      next.transformerModel = settings.transformer_model;
    }
    if (Object.keys(next).length > 0) {
      setState((prev) => ({ ...prev, ...next }));
    }
  }, [
    settings.model,
    settings.transformer_model,
    state.apiModel,
    state.localModel,
    setState,
  ]);

  const openDownloadsTray = () => {
    try {
      localStorage.setItem("downloadTrayExpanded", "true");
      const bc = new BroadcastChannel("model-download");
      bc.postMessage({ type: "tray:toggle", payload: true });
      bc.close();
    } catch {}
  };



  // Model downloads have been moved to the Models pane (ModelManager).



  // Model deletion is handled in the Models pane now.



  // Retain size polling for Settings’ local display if needed

  // Download progress polling removed from Settings; handled by DownloadTray.
  const availableDevices =
    (Array.isArray(settings.devices) && settings.devices.length
      ? settings.devices
      : Array.isArray(state.devices)
        ? state.devices
        : []) || [];
  const defaultDeviceObject =
    (settings.default_device && typeof settings.default_device === "object"
      ? settings.default_device
      : state.defaultDevice && typeof state.defaultDevice === "object"
        ? state.defaultDevice
        : null);
  const defaultDeviceId = defaultDeviceObject
    ? defaultDeviceObject.id || defaultDeviceObject.name || null
    : null;
  const defaultDeviceName = defaultDeviceObject
    ? defaultDeviceObject.name || defaultDeviceObject.id || null
    : null;
  const selectedInferenceId =
    settings.inference_device || defaultDeviceId || "";
  const selectedDevice =
    availableDevices.find(
      (device) =>
        device &&
        (device.id === selectedInferenceId ||
          device.name === selectedInferenceId),
    ) ||
    availableDevices.find(
      (device) =>
        device &&
        (device.id === defaultDeviceId || device.name === defaultDeviceName),
    ) ||
    null;
  const selectedDeviceSummary = selectedDevice
    ? [
        selectedDevice.name || selectedDevice.id || "Unknown device",
        selectedDevice.type
          ? String(selectedDevice.type).toUpperCase()
          : null,
        typeof selectedDevice.total_memory_gb === "number" &&
        Number.isFinite(selectedDevice.total_memory_gb)
          ? `${selectedDevice.total_memory_gb} GB`
          : null,
      ]
        .filter(Boolean)
        .join(" • ")
    : "";

  const selectedProviderKey = normalizeModelId(settings.local_provider) || "lmstudio";
  const managedLocalRuntimeSelected =
    settings.mode === "local" && isLocalRuntimeEntry(settings.transformer_model);
  const directLocalRuntimeSelected =
    settings.mode === "local" && !managedLocalRuntimeSelected;
  const providerRuntimeInspectable =
    settings.mode === "local" && MANAGED_LOCAL_PROVIDERS.has(selectedProviderKey);

  const cudaDiagnostics =
    (settings.cuda_diagnostics &&
      typeof settings.cuda_diagnostics === "object" &&
      settings.cuda_diagnostics !== null
      ? settings.cuda_diagnostics
      : state.cudaDiagnostics &&
          typeof state.cudaDiagnostics === "object" &&
          state.cudaDiagnostics !== null
        ? state.cudaDiagnostics
        : null);

  const selectedDeviceIsCuda =
    !!(
      selectedDevice &&
      typeof selectedDevice.type === "string" &&
      selectedDevice.type.toLowerCase() === "cuda"
    );

  const torchCudaAvailable = !!(cudaDiagnostics && cudaDiagnostics.cuda_available);

  const baseCudaStatus = cudaDiagnostics
    ? cudaDiagnostics.status || (torchCudaAvailable ? "online" : "offline")
    : "loading";

  const badgeStatus = selectedDeviceIsCuda
    ? baseCudaStatus
    : torchCudaAvailable
      ? "online"
      : baseCudaStatus;

  let cudaBadgeVariant = "status-badge--loading";
  let cudaBadgeLabel = "cuda pending";
  let cudaBadgeTitle = "CUDA diagnostics are loading.";
  let cudaBadgeNote = "";

  if (!cudaDiagnostics) {
    cudaBadgeVariant = "status-badge--loading";
  } else {
    const status =
      badgeStatus === "loading"
        ? cudaDiagnostics.status || "offline"
        : badgeStatus;
    if (status === "online") {
      cudaBadgeVariant = "status-badge--online";
      cudaBadgeLabel = selectedDeviceIsCuda ? "cuda ready" : "cuda runtime ready";
    } else if (status === "degraded") {
      cudaBadgeVariant = "status-badge--degraded";
      cudaBadgeLabel = selectedDeviceIsCuda ? "cuda mismatch" : "cuda limited";
      cudaBadgeNote =
        cudaDiagnostics.note ||
        "GPU detected but the current PyTorch build lacks CUDA support.";
    } else {
      cudaBadgeVariant = "status-badge--offline";
      cudaBadgeLabel = "cuda unavailable";
      cudaBadgeNote =
        cudaDiagnostics.note || "PyTorch reports that CUDA is unavailable.";
    }
    const titleParts = [];
    if (cudaDiagnostics.cuda_runtime_version) {
      titleParts.push(`CUDA ${cudaDiagnostics.cuda_runtime_version}`);
    }
    if (cudaDiagnostics.torch_version) {
      titleParts.push(`torch ${cudaDiagnostics.torch_version}`);
    }
    if (selectedDeviceIsCuda) {
      titleParts.push(
        selectedDevice.name ||
          selectedDevice.id ||
          cudaDiagnostics.detected_device_names?.[0] ||
          "cuda device",
      );
    } else if (
      Array.isArray(cudaDiagnostics.detected_device_names) &&
      cudaDiagnostics.detected_device_names.length > 0
    ) {
      titleParts.push(cudaDiagnostics.detected_device_names[0]);
    }
    if (titleParts.length > 0) {
      cudaBadgeTitle = titleParts.join(" • ");
    } else {
      cudaBadgeTitle = `CUDA status: ${status}`;
    }
  }

  const cudaBadgeClass = `status-badge ${cudaBadgeVariant}`;
  const cudaNoteWarn =
    badgeStatus === "degraded" ||
    badgeStatus === "offline" ||
    badgeStatus === "loading";

  const providerRuntimeStatus = !providerRuntimeInspectable
    ? "offline"
    : providerRuntimeLoading && !providerRuntime
      ? "loading"
      : providerRuntimeError
        ? "offline"
        : providerRuntime?.server_running
          ? providerRuntime?.model_loaded
            ? "online"
            : "degraded"
          : "offline";
  const providerRuntimeSummary = providerRuntime?.loaded_model
    ? `Loaded: ${providerRuntime.loaded_model}`
    : providerRuntime?.server_running
      ? "Server is running without a model loaded."
      : "Server is not running.";
  const providerRuntimeDetail = !providerRuntimeInspectable
    ? "Inventory polling is only available for LM Studio and Ollama."
    : providerRuntimeError
      ? providerRuntimeError
      : providerModelOptions.length > 0
        ? `${providerModelOptions.length} provider models reported.`
        : providerRuntimeLoading
          ? "Checking provider runtime…"
          : "No provider models reported yet.";

  const fieldTooltips = {

    transformer_model: "Language model for local inference or downloaded weights.",

    stt_model: "Speech-to-text model for transcribing audio.",

    tts_model: "Text-to-speech voice synthesis engine.",

    voice_model: "Voice preset for TTS (OpenAI, Kitten, Kokoro).",
    stream_backend: "Backend used when you start live streaming from chat.",
    realtime_model: "OpenAI Realtime model used for live streaming sessions.",
    realtime_voice: "Voice used by OpenAI Realtime during live streaming sessions.",

    vision_model: "Local caption and image-fallback model used when chat vision is not natively available.",

    rag_embedding_model: "Text embedding model used for semantic RAG search.",

  };

  const voiceOptionsForTts = useMemo(() => {
    const tts = String(settings.tts_model || "").toLowerCase();
    if (!tts) return voicePresetOptions;
    if (tts.includes("tts-1")) return openAiVoiceOptions;
    if (tts.includes("kokoro")) return kokoroVoiceOptions;
    if (tts.includes("kitten")) return kittenVoiceOptions;
    return voicePresetOptions;
  }, [settings.tts_model]);

  const isKnownVoicePreset =
    !settings.voice_model ||
    voiceOptionsForTts.includes(settings.voice_model);

  const voicePresetInput = (
    <div className="model-inline-group">
      <div className="model-inline voice-inline">
        <span className="model-inline-label">Voice</span>
        <input
          name="voice_model"
          value={settings.voice_model || ""}
          onChange={handleChange}
          list="voice-preset-options"
          placeholder="voice preset"
          title={fieldTooltips.voice_model}
          aria-label="Voice preset"
        />
        <datalist id="voice-preset-options">
          {voiceOptionsForTts.map((voice) => (
            <option key={voice} value={voice} />
          ))}
        </datalist>
      </div>
      {!isKnownVoicePreset && (
        <div className="status-note warn form-note">
          Voice preset doesn’t match the selected TTS model. Choose a preset or
          leave blank for provider defaults.
        </div>
      )}
      <div className="status-note form-note">
        <em>OpenAI API voices use `tts-1` or `tts-1-hd`. `kitten` and `kokoro` use local voice presets.</em>
      </div>
    </div>
  );



  // Local preference for Harmony defaulting while editing (before Save)

  const isHarmonyPreferred = (...models) => {

    try {

      const generic = ["gpt-4o", "gpt-4.1", "gpt-5-mini", "gpt-5", "gpt-5.1", "gpt-5.2"];

      return models

        .filter(Boolean)

        .map((model) => String(model).toLowerCase())

        .some((m) => isGptOssModel(m) || generic.some((g) => m.startsWith(g)));

    } catch {

      return false;

    }

  };



  useEffect(() => {

    // Auto-toggle harmony_format in the form when model changes, unless user overrode

    if (!state.harmonyTouched) {

      const preferred = isHarmonyPreferred(

        settings.transformer_model,

        settings.model,

      );

      if (preferred !== settings.harmony_format) {

        setSettings((prev) => ({ ...prev, harmony_format: preferred }));

      }

    }

  }, [

    settings.transformer_model,

    settings.model,

    settings.harmony_format,

    state.harmonyTouched,

  ]);

  const preferHarmony = isHarmonyPreferred(

    settings.transformer_model,

    settings.model,

  );

  const harmonyWarning = preferHarmony && !settings.harmony_format;

  const harmonyAttentionModels = [settings.model, settings.transformer_model]

    .filter(

      (m, idx, arr) =>

        typeof m === "string" &&

        isGptOssModel(m) &&

        arr.findIndex(

          (other) =>

            typeof other === "string" &&

            other.toLowerCase() === m.toLowerCase(),

        ) === idx,

    )

    .map((m) => String(m));

  const harmonyWarningMessage = harmonyWarning

    ? harmonyAttentionModels.length > 0

      ? `Harmony Formatting is recommended for ${harmonyAttentionModels.join(", ")} to keep tool metadata intact.`

      : "Harmony Formatting is recommended for GPT-OSS models (gpt-oss-20b, gpt-oss-120b) to keep tool metadata intact."

    : "";



  // Schedule a background download job and broadcast to global DownloadTray

  const scheduleDownloadJob = async (model) => {

    const body = {

      model,

      ...(useCustomModelsFolder && settings.models_folder

        ? { path: settings.models_folder }

        : {}),

    };

    const r = await axios.post("/api/models/jobs", body);

    const job = r.data?.job;

    if (job?.id) {

      const key = "modelDownloadJobs";

      const list = JSON.parse(localStorage.getItem(key) || "[]");

      const entry = {

        id: job.id,

        model,

        path: job.path,

        status: job.status,

        total: job.total || 0,

        downloaded: r.data?.downloaded || 0,

        percent: r.data?.percent || 0,

      };

      const next = [entry, ...list.filter((j) => j.id !== job.id)];

      localStorage.setItem(key, JSON.stringify(next));

      try {

        const bc = new BroadcastChannel("model-download");

        bc.postMessage({ type: "jobs:update", payload: next });

        bc.close();

      } catch {}

    }

  };



  const handleModelDownload = async (field) => {

    const model = settings[field];

    if (!model) return;

    try {

      setDownloadingModel((prev) => ({ ...prev, [field]: true }));

      const requiresAuth = !!modelInfos[field]?.requires_auth;
      const hasToken =
        (settings.hf_token && settings.hf_token.trim()) || settings.hf_token_set;
      if (requiresAuth && !hasToken) {
        alert(
          "This model is gated. Add a Hugging Face token in Settings and accept the license on the repo page.",
        );
        return;
      }

      await scheduleDownloadJob(model);

      // Refresh quick availability list

      fetchAvailableModels(useCustomModelsFolder ? settings.models_folder : undefined);

    } catch (err) {

      const status = err?.response?.status;

      const msg = err?.response?.data?.detail || err?.message || "Model download failed.";

      alert(msg);

      // Assist gated repos by opening the model page to login/accept license

      if (status === 403) {

        try {

          if (model.startsWith("gemma-3")) {

            window.open(`https://huggingface.co/google/${model}`, "_blank");

          } else if (model.startsWith("clip-vit")) {

            window.open(`https://huggingface.co/openai/${model}`, "_blank");

          }

        } catch {}

      }

    } finally {

      setDownloadingModel((prev) => ({ ...prev, [field]: false }));

    }

  };



  const handleModelDelete = async (field) => {

    const model = settings[field];

    if (!model) return;

    try {

      setDownloadingModel((prev) => ({ ...prev, [field]: true }));

      await axios.delete(`/api/models/${model}`,

        useCustomModelsFolder && settings.models_folder

          ? { params: { path: settings.models_folder } }

          : undefined,

      );

      // Invalidate availability and sizes for this field

      setModelAvailable((prev) => ({ ...prev, [field]: false }));

      setModelLocalSizes((prev) => ({ ...prev, [field]: 0 }));

      fetchAvailableModels(useCustomModelsFolder ? settings.models_folder : undefined);

    } catch (err) {

      alert("Model deletion failed.");

    } finally {

      setDownloadingModel((prev) => ({ ...prev, [field]: false }));

    }

  };

  const handleRegisterLocalModel = async () => {
    const path = String(registerModelPath || "").trim();
    if (!path) {
      setRegisterModelMessage("Path is required.");
      return;
    }
    const payload = {
      path,
      model_type: registerModelType || "transformer",
    };
    const alias = String(registerModelAlias || "").trim();
    if (alias) {
      payload.alias = alias;
    }
    setRegisterModelBusy(true);
    setRegisterModelMessage("");
    try {
      const res = await axios.post("/api/models/registered", payload);
      const savedAlias = String(res?.data?.model?.alias || alias || "").trim();
      await fetchAvailableModels(
        useCustomModelsFolder ? settings.models_folder : undefined,
      );
      await fetchRegisteredLocalModels();
      setRegisterModelAlias("");
      setRegisterModelPath("");
      setRegisterModelMessage(
        savedAlias
          ? `Registered local model '${savedAlias}'.`
          : "Registered local model.",
      );
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to register local model.";
      setRegisterModelMessage(String(detail));
    } finally {
      setRegisterModelBusy(false);
    }
  };

  const handleUnregisterLocalModel = async (alias) => {
    const modelAlias = String(alias || "").trim();
    if (!modelAlias) return;
    setRegisterModelBusy(true);
    setRegisterModelMessage("");
    try {
      await axios.delete(`/api/models/registered/${encodeURIComponent(modelAlias)}`);
      await fetchAvailableModels(
        useCustomModelsFolder ? settings.models_folder : undefined,
      );
      await fetchRegisteredLocalModels();
      const fallbackTransformerModel =
        (Array.isArray(suggestedLangModels) && suggestedLangModels[0]) ||
        "gpt-oss-20b";
      const removedWasSelected =
        settings.transformer_model === modelAlias ||
        state.localModel === modelAlias ||
        state.transformerModel === modelAlias;
      if (removedWasSelected && fallbackTransformerModel !== modelAlias) {
        setSettings((prev) => ({
          ...prev,
          transformer_model: fallbackTransformerModel,
        }));
        setState((prev) => ({
          ...prev,
          localModel:
            prev.localModel === modelAlias
              ? fallbackTransformerModel
              : prev.localModel,
          transformerModel:
            prev.transformerModel === modelAlias
              ? fallbackTransformerModel
              : prev.transformerModel,
        }));
        axios
          .post("/api/settings", { transformer_model: fallbackTransformerModel })
          .catch(() => {});
      }
      setRegisterModelMessage(`Removed local model '${modelAlias}'.`);
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to remove local model.";
      setRegisterModelMessage(String(detail));
    } finally {
      setRegisterModelBusy(false);
    }
  };



  // Build a normalized comparable object for change detection and saving

  const buildComparable = (s, useModelsFolder, useConvFolder) => {

    const obj = {

      api_key: s.api_key,
      hf_token: s.hf_token,

      api_url: s.api_url,

      local_url: s.local_url,

      mode: s.mode,

      openai_model: s.model,

      dynamic_model: s.dynamic_model,

      dynamic_port: s.dynamic_port ? parseInt(s.dynamic_port, 10) : null,

      inference_device: s.inference_device || null,

      transformer_model: s.transformer_model,
      local_provider: s.local_provider || "lmstudio",
      local_provider_mode: s.local_provider_mode || "local-managed",
      local_provider_base_url: s.local_provider_base_url || "",
      local_provider_host: s.local_provider_host || "127.0.0.1",
      local_provider_port:
        typeof s.local_provider_port === "number" ? s.local_provider_port : 1234,
      lmstudio_path: s.lmstudio_path || "",
      local_provider_api_token: s.local_provider_api_token || "",
      local_provider_auto_start: !!s.local_provider_auto_start,
      local_provider_preferred_model: s.local_provider_preferred_model || "",
      local_provider_default_context_length:
        typeof s.local_provider_default_context_length === "number" &&
        s.local_provider_default_context_length > 0
          ? s.local_provider_default_context_length
          : null,
      local_provider_show_server_logs: !!s.local_provider_show_server_logs,
      local_provider_enable_cors: !!s.local_provider_enable_cors,
      local_provider_allow_lan: !!s.local_provider_allow_lan,

      static_model: s.static_model,

      harmony_format: s.harmony_format,

      server_url: s.server_url,

      stt_model: s.stt_model,

      tts_model: s.tts_model,

      voice_model: s.voice_model,
      stream_backend: s.stream_backend || "api",
      realtime_model: s.realtime_model || "",
      realtime_voice: s.realtime_voice || "",
      realtime_base_url: s.realtime_base_url || "",
      realtime_connect_url: s.realtime_connect_url || "",

      vision_model: s.vision_model,

      max_context_length: s.context_length,

      kv_cache: s.kv_cache,

      ram_swap: s.ram_swap,
      request_timeout:
        typeof s.request_timeout === "number" ? s.request_timeout : null,
      stream_idle_timeout:
        typeof s.stream_idle_timeout === "number" ? s.stream_idle_timeout : null,
      device_map_strategy: s.device_map_strategy || "auto",
      gpu_memory_fraction:
        typeof s.gpu_memory_fraction === "number" ? s.gpu_memory_fraction : 0,
      gpu_memory_margin_mb:
        typeof s.gpu_memory_margin_mb === "number" ? s.gpu_memory_margin_mb : 0,
      gpu_memory_limit_gb:
        typeof s.gpu_memory_limit_gb === "number" ? s.gpu_memory_limit_gb : 0,
      cpu_offload_fraction:
        typeof s.cpu_offload_fraction === "number" ? s.cpu_offload_fraction : 0,
      cpu_offload_limit_gb:
        typeof s.cpu_offload_limit_gb === "number"
          ? s.cpu_offload_limit_gb
          : 0,
      flash_attention: !!s.flash_attention,
      attention_implementation: s.attention_implementation || "",
      kv_cache_implementation: s.kv_cache_implementation || "",
      kv_cache_quant_backend: s.kv_cache_quant_backend || "",
      kv_cache_dtype: s.kv_cache_dtype || "",
      kv_cache_device: s.kv_cache_device || "",
      model_dtype: s.model_dtype || "",
      cpu_thread_count:
        typeof s.cpu_thread_count === "number" ? s.cpu_thread_count : 0,
      low_cpu_mem_usage: !!s.low_cpu_mem_usage,

      rag_embedding_model: s.rag_embedding_model || "local:all-MiniLM-L6-v2",
      rag_clip_model: s.rag_clip_model || "ViT-B-32",
      rag_chat_min_similarity:
        typeof s.rag_chat_min_similarity === "number"
          ? s.rag_chat_min_similarity
          : 0.3,
      sae_threads_signal_mode:
        typeof s.sae_threads_signal_mode === "string"
          ? s.sae_threads_signal_mode
          : "hybrid",
      sae_threads_signal_blend:
        typeof s.sae_threads_signal_blend === "number"
          ? Math.min(1, Math.max(0, s.sae_threads_signal_blend))
          : 0.7,
      sae_model_combo: s.sae_model_combo || "",
      sae_embeddings_fallback:
        typeof s.sae_embeddings_fallback === "boolean"
          ? s.sae_embeddings_fallback
          : true,
      sae_steering_enabled: !!s.sae_steering_enabled,
      sae_steering_layer:
        typeof s.sae_steering_layer === "number" ? s.sae_steering_layer : 12,
      sae_steering_features: s.sae_steering_features || "",
      sae_steering_token_positions: s.sae_steering_token_positions || "last",
      sae_steering_dry_run:
        typeof s.sae_steering_dry_run === "boolean"
          ? s.sae_steering_dry_run
          : true,
      sae_live_inspect_console: !!s.sae_live_inspect_console,
      weaviate_url: s.weaviate_url,

      weaviate_auto_start: !!s.weaviate_auto_start,

      // UI-only values that we still apply on Save via setState

      approvalLevel: s.approvalLevel,

    };

    if (useConvFolder && s.conv_folder) {

      obj.conv_folder = s.conv_folder;

    }

    if (useModelsFolder && s.models_folder) {

      obj.models_folder = s.models_folder;

    }

    return obj;

  };



  const comparable = useMemo(

    () => buildComparable(settings, useCustomModelsFolder, useCustomConvFolder),

    [settings, useCustomModelsFolder, useCustomConvFolder]

  );



  // Initialize the baseline for dirty-checking after initial auto-defaults settle

  useEffect(() => {

    if (!loading && !initialized) {

      const t = setTimeout(() => {

        setInitialComparable(

          buildComparable(settings, useCustomModelsFolder, useCustomConvFolder),

        );

        setInitialized(true);

      }, 0);

      return () => clearTimeout(t);

    }

  }, [

    loading,

    initialized,

    settings,

    useCustomModelsFolder,

    useCustomConvFolder,

  ]);



  const isDirty = useMemo(() => {

    if (!initialized || !initialComparable) return false;

    try {

      return JSON.stringify(comparable) !== JSON.stringify(initialComparable);

    } catch {

      return true;

    }

  }, [initialized, initialComparable, comparable]);



  const renderModelField = (label, field, suggestions = [], extra = null) => {

    const model = settings[field] || "";

    const downloadBlocked = NON_DOWNLOADABLE.has(model);

    const available = modelAvailable[field];

    const info = modelInfos[field] || { size: 0, repo_id: null };

    const repoId = info.repo_id || null;
    const requiresAuth = !!info.requires_auth;

    const expectedBytes =

      (info.size && info.size > 0 ? info.size : modelExpectedBytes[field] || 0);

    const expectedSizeGB =

      expectedBytes > 0 ? `${(expectedBytes / 1024 ** 3).toFixed(2)} gb` : "--";

    const localBytes = modelLocalSizes[field] || 0;

    const verified = !!modelVerified[field];

    const installedSizeGB =

      localBytes > 0 ? `${(localBytes / 1024 ** 3).toFixed(2)} gb` : "--";

    const modelIsApiOnly = API_ONLY.has(model);

    const downloadable = !modelIsApiOnly && (modelDownloadable[field] ?? true);

    const optionMeta = (m) => {
      const value = typeof m === "string" ? m.trim() : "";
      const isApiOnly = Boolean(value && API_ONLY.has(value));
      const isSuggested = Boolean(value && suggestions.includes(value));
      const isAvailable = Boolean(value && availableModelSet.has(value));
      const isRegistered = Boolean(value && registeredModelAliasSet.has(value));
      const className = `model-option ${
        isApiOnly
          ? "model-option-api"
          : isAvailable
            ? "model-option-available"
            : isSuggested
              ? "model-option-suggested"
              : "model-option-unknown"
      }`;
      const labelText = isApiOnly
        ? `${value} (API)`
        : isAvailable
          ? `✓ ${value}`
          : isSuggested
            ? `☆ ${value}`
            : value;
      const providerLabel = describeModelProvider(field, value);
      return {
        value,
        className,
        labelText: [
          isRegistered ? `${labelText} (local)` : labelText,
          providerLabel ? `\u00b7 ${providerLabel}` : "",
        ]
          .filter(Boolean)
          .join(" "),
      };
    };

    const registeredOptions = registeredModelOptionsByField[field] || [];
    const options = [

      ...suggestions,

      ...registeredOptions.filter((m) => !suggestions.includes(m)),

      ...filterAvailableModelsForField(field, availableModels, {
        includeAll: includeCacheUnfiltered,
      }).filter((m) => !suggestions.includes(m) && !registeredOptions.includes(m)),

    ];

    return (

      <>

        <label title={fieldTooltips[field] || label}>{label}</label>

        <div
          className={`model-select-row ${
            available ? "model-present" : "model-missing"
          }${extra ? " has-inline" : ""}`}
        >

          <select

            name={field}

            value={model}

            onChange={handleChange}

            title={fieldTooltips[field] || `Select ${label}`}

          >

            {options.map((m) => {
              const meta = optionMeta(m);
              return (

              <option key={m} value={m} className={meta.className}>

                {meta.labelText}

              </option>

              );
            })}

            {!options.includes(model) && model && (

              <option value={model} className={optionMeta(model).className}>
                {optionMeta(model).labelText}
              </option>

            )}

          </select>

          <button

            type="button"

            className="icon-btn"

            title={

              downloadBlocked

                ? "Not downloadable (external/API-only)"

                : requiresAuth
                  ? "Requires Hugging Face auth"
                  : !downloadable
                    ? "Download not available (API-only)"
                    : available && !verified

                    ? "Repair download"

                    : "Download model"

            }

            onClick={() => handleModelDownload(field)}

            disabled={
              !!downloadingModel[field] ||
              downloadBlocked ||
              !downloadable ||
              (available && verified)
            }

          >

            ⬇️

          </button>

          {repoId && !String(repoId).startsWith("TODO") && (

            <button

              type="button"

              className="icon-btn"

              title="Open model page"

              onClick={() => window.open(`https://huggingface.co/${repoId}`, "_blank")}

            >

              🔗

            </button>

          )}

          <button

            type="button"

            className="icon-btn"

            title={available ? "Open containing folder" : "Model not present"}

            onClick={async () => {

              try {

                await axios.get(

                  `/api/models/reveal/${model}`,

                  useCustomModelsFolder && settings.models_folder

                    ? { params: { path: settings.models_folder } }

                    : undefined,

                );

              } catch (e) {

                alert("Unable to open folder on host.");

              }

            }}

            disabled={!available}

          >

            📂

          </button>

          <button

            type="button"

            className="icon-btn"

            title="Delete model"

            onClick={() => handleModelDelete(field)}

            disabled={!!downloadingModel[field] || !available || downloadBlocked}

          >

            🗑️

          </button>

          <span

            className="model-size"

            title={verified ? "expected / installed size (checksum verified)" : "expected size / installed (checksum pending)"}

          >

            {expectedSizeGB} / {installedSizeGB}{verified ? " ✓" : ""}

          </span>
          {extra}

        </div>

      </>

    );

  };



  const handleSave = () => {

    setSaving(true);

    setMessage("");

    const payload = {

      ...(settings.api_key && settings.api_key.trim()
        ? { api_key: settings.api_key.trim() }
        : {}),
      ...(settings.hf_token && settings.hf_token.trim()
        ? { hf_token: settings.hf_token.trim() }
        : {}),

      api_url: settings.api_url,

      local_url: settings.local_url,

      mode: settings.mode,

      openai_model: settings.model,

      dynamic_model: settings.dynamic_model,

      dynamic_port: settings.dynamic_port

        ? parseInt(settings.dynamic_port, 10)

        : null,

      inference_device: settings.inference_device || null,

      ...(useCustomConvFolder && settings.conv_folder

        ? { conv_folder: settings.conv_folder }

        : {}),

      // only persist custom models folder when explicitly enabled

      ...(useCustomModelsFolder && settings.models_folder

        ? { models_folder: settings.models_folder }

        : {}),

      transformer_model: settings.transformer_model,
      local_provider: settings.local_provider || "lmstudio",
      local_provider_mode: settings.local_provider_mode || "local-managed",
      local_provider_base_url: settings.local_provider_base_url || "",
      local_provider_host: settings.local_provider_host || "127.0.0.1",
      local_provider_port: settings.local_provider_port,
      lmstudio_path: settings.lmstudio_path || "",
      ...(settings.local_provider_api_token &&
      settings.local_provider_api_token.trim()
        ? { local_provider_api_token: settings.local_provider_api_token.trim() }
        : {}),
      local_provider_auto_start: !!settings.local_provider_auto_start,
      local_provider_preferred_model:
        settings.local_provider_preferred_model || "",
      local_provider_default_context_length:
        settings.local_provider_default_context_length,
      local_provider_show_server_logs: !!settings.local_provider_show_server_logs,
      local_provider_enable_cors: !!settings.local_provider_enable_cors,
      local_provider_allow_lan: !!settings.local_provider_allow_lan,

      static_model: settings.static_model,

      harmony_format: settings.harmony_format,

      server_url: settings.server_url,

      stt_model: settings.stt_model,

      tts_model: settings.tts_model,

      voice_model: settings.voice_model,
      stream_backend: settings.stream_backend || "api",
      realtime_model: settings.realtime_model,
      realtime_voice: settings.realtime_voice,
      realtime_base_url: settings.realtime_base_url,
      realtime_connect_url: settings.realtime_connect_url,

      vision_model: settings.vision_model,

      max_context_length: settings.context_length,

      kv_cache: settings.kv_cache,

      ram_swap: settings.ram_swap,
      request_timeout: settings.request_timeout,
      stream_idle_timeout: settings.stream_idle_timeout,
      device_map_strategy: settings.device_map_strategy,
      gpu_memory_fraction: settings.gpu_memory_fraction,
      gpu_memory_margin_mb: settings.gpu_memory_margin_mb,
      gpu_memory_limit_gb: settings.gpu_memory_limit_gb,
      cpu_offload_fraction: settings.cpu_offload_fraction,
      cpu_offload_limit_gb: settings.cpu_offload_limit_gb,
      flash_attention: settings.flash_attention,
      attention_implementation: settings.attention_implementation,
      kv_cache_implementation: settings.kv_cache_implementation,
      kv_cache_quant_backend: settings.kv_cache_quant_backend,
      kv_cache_dtype: settings.kv_cache_dtype,
      kv_cache_device: settings.kv_cache_device,
      model_dtype: settings.model_dtype,
      cpu_thread_count: settings.cpu_thread_count,

      // RAG / Weaviate

      rag_embedding_model: settings.rag_embedding_model,
      rag_clip_model: settings.rag_clip_model,
      rag_chat_min_similarity: settings.rag_chat_min_similarity,
      sae_threads_signal_mode: settings.sae_threads_signal_mode,
      sae_threads_signal_blend: settings.sae_threads_signal_blend,
      sae_model_combo: settings.sae_model_combo,
      sae_embeddings_fallback: !!settings.sae_embeddings_fallback,
      sae_steering_enabled: !!settings.sae_steering_enabled,
      sae_steering_layer: settings.sae_steering_layer,
      sae_steering_features: settings.sae_steering_features,
      sae_steering_token_positions: settings.sae_steering_token_positions,
      sae_steering_dry_run: !!settings.sae_steering_dry_run,
      sae_live_inspect_console: !!settings.sae_live_inspect_console,
      weaviate_url: settings.weaviate_url,

      weaviate_auto_start: !!settings.weaviate_auto_start,

    };

    axios

      .post("/api/settings", payload)

      .then(() => {

        setMessage("Settings saved successfully.");

        // After a successful save, reset the baseline so Save re-disables

        const storedKey = settings.api_key && settings.api_key.trim();
        const storedHfToken = settings.hf_token && settings.hf_token.trim();
        const storedProviderToken =
          settings.local_provider_api_token &&
          settings.local_provider_api_token.trim();
        let nextSettings = settings;
        if (storedKey) {
          nextSettings = {
            ...nextSettings,
            api_key: "",
            api_key_set: true,
            api_key_preview:
              settings.api_key_preview ||
              `${storedKey.slice(0, 3)}...${storedKey.slice(-4)}`,
          };
        }
        if (storedHfToken) {
          nextSettings = {
            ...nextSettings,
            hf_token: "",
            hf_token_set: true,
            hf_token_preview:
              settings.hf_token_preview ||
              `${storedHfToken.slice(0, 3)}...${storedHfToken.slice(-4)}`,
          };
        }
        if (storedProviderToken) {
          nextSettings = {
            ...nextSettings,
            local_provider_api_token: "",
            local_provider_api_token_set: true,
            local_provider_api_token_preview:
              settings.local_provider_api_token_preview ||
              `${storedProviderToken.slice(0, 3)}...${storedProviderToken.slice(-4)}`,
          };
        }
        const normalizedRealtimeDefaults =
          !nextSettings.stream_backend ||
          !nextSettings.realtime_model ||
          !nextSettings.realtime_voice ||
          !nextSettings.realtime_base_url ||
          !nextSettings.realtime_connect_url;
        if (normalizedRealtimeDefaults) {
          nextSettings = {
            ...nextSettings,
            stream_backend: nextSettings.stream_backend || "api",
            realtime_model: nextSettings.realtime_model || "gpt-realtime",
            realtime_voice: nextSettings.realtime_voice || "alloy",
            realtime_base_url:
              nextSettings.realtime_base_url ||
              "https://api.openai.com/v1/realtime/client_secrets",
            realtime_connect_url:
              nextSettings.realtime_connect_url ||
              "https://api.openai.com/v1/realtime/calls",
          };
        }
        if (
          storedKey ||
          storedHfToken ||
          storedProviderToken ||
          normalizedRealtimeDefaults
        ) {
          setSettings(nextSettings);
        }
        setInitialComparable(
          buildComparable(nextSettings, useCustomModelsFolder, useCustomConvFolder),
        );

        setInitialized(true);

      })

      .catch(() => {

        setMessage("Error saving settings.");

      })

      .finally(() => {

        setSaving(false);

        setState((prev) => ({

          ...prev,

          backendMode: settings.mode,

          devices: settings.devices,

          defaultDevice: settings.default_device,

          cudaDiagnostics:
            settings.cuda_diagnostics ?? prev.cudaDiagnostics,

          inferenceDevice:
            settings.inference_device ??
            prev.inferenceDevice ??
            (settings.default_device
              ? settings.default_device.id || settings.default_device.name
              : null),

          apiModel: settings.model,

          localModel: settings.transformer_model,

          transformerModel: settings.transformer_model,

          staticModel: settings.static_model,

          approvalLevel: settings.approvalLevel,

          harmonyFormat: settings.harmony_format,

          serverUrl: settings.server_url,

          sttModel: settings.stt_model,

          ttsModel: settings.tts_model,

          voiceModel: settings.voice_model,

          visionModel: settings.vision_model,

          maxContextLength: settings.context_length,

          kvCache: settings.kv_cache,

          ramSwap: settings.ram_swap,

        }));

      });

  };

  const fetchRegisteredLocalModels = () => {
    return axios
      .get("/api/models/registered")
      .then((r) => {
        const entries = Array.isArray(r?.data?.models) ? r.data.models : [];
        setRegisteredLocalModels(entries);
        setState((prev) => ({ ...prev, registeredLocalModels: entries }));
      })
      .catch(() => {
        setRegisteredLocalModels([]);
        setState((prev) => ({ ...prev, registeredLocalModels: [] }));
      });
  };

  const handleExportDefaultsSave = async () => {
    setExportSaving(true);
    setExportMessage("");
    try {
      await axios.post("/api/user-settings", {
        export_default_format: normalizeExportFormat(exportDefaults.format),
        export_default_include_chat: !!exportDefaults.includeChat,
        export_default_include_thoughts: !!exportDefaults.includeThoughts,
        export_default_include_tools: !!exportDefaults.includeTools,
      });
      setExportMessage("Export defaults saved.");
    } catch {
      setExportMessage("Failed to save export defaults.");
    } finally {
      setExportSaving(false);
    }
  };

  const handleSyncDefaultsSave = async () => {
    setSyncDefaultsSaving(true);
    setSyncMessage("");
    try {
      await axios.post("/api/user-settings", {
        sync_link_to_source_device: !!syncLinkToSourceDevice,
        sync_source_namespace: syncSourceNamespace.trim(),
      });
      setSyncMessage("Sync defaults saved.");
    } catch {
      setSyncMessage("Failed to save sync defaults.");
    } finally {
      setSyncDefaultsSaving(false);
    }
  };

  const handleSystemPromptSave = async () => {
    setSystemPromptSaving(true);
    setSystemPromptMessage("");
    try {
      await axios.post("/api/user-settings", {
        system_prompt_base: systemPromptBase,
        system_prompt_custom: systemPromptCustom,
      });
      setSystemPromptMessage("System prompt customization saved.");
    } catch {
      setSystemPromptMessage("Failed to save system prompt customization.");
    } finally {
      setSystemPromptSaving(false);
    }
  };

  const handleToolResolutionNotificationsChange = async (event) => {
    const checked = !!event.target.checked;
    setToolResolutionNotifications(checked);
    setNotificationPrefMessage("");
    try {
      await axios.post("/api/user-settings", {
        tool_resolution_notifications: checked,
      });
      setNotificationPrefMessage("Tool review notifications saved.");
    } catch (err) {
      setToolResolutionNotifications(!checked);
      setNotificationPrefMessage("Failed to save tool review notifications.");
    }
  };

  const handleActionHistorySave = async () => {
    setActionHistorySaving(true);
    setActionHistoryMessage("");
    try {
      await axios.post("/api/user-settings", {
        action_history_retention_days: Number(actionHistoryRetentionDays) || 0,
      });
      setActionHistoryMessage("Work history retention saved.");
    } catch {
      setActionHistoryMessage("Failed to save work history retention.");
    } finally {
      setActionHistorySaving(false);
    }
  };

  const handleCaptureWorkflowSave = async () => {
    const nextModules = Array.from(
      new Set(
        (Array.isArray(enabledWorkflowModules) ? enabledWorkflowModules : [])
          .map((item) => String(item || "").trim())
          .filter(Boolean),
      ),
    );
    const nextWorkflow = String(defaultWorkflow || "default").trim() || "default";
    const nextRetentionDays = Math.max(1, Number(captureRetentionDays) || 7);
    setCaptureWorkflowSaving(true);
    setCaptureWorkflowMessage("");
    try {
      await axios.post("/api/user-settings", {
        capture_retention_days: nextRetentionDays,
        capture_default_sensitivity: captureDefaultSensitivity || "personal",
        capture_allow_model_raw_image_access: captureAllowModelRawImageAccess !== false,
        capture_allow_summary_fallback: captureAllowSummaryFallback !== false,
        default_workflow: nextWorkflow,
        enabled_workflow_modules: nextModules,
      });
      setState((prev) => ({
        ...prev,
        captureRetentionDays: nextRetentionDays,
        captureDefaultSensitivity: captureDefaultSensitivity || "personal",
        captureAllowModelRawImageAccess: captureAllowModelRawImageAccess !== false,
        captureAllowSummaryFallback: captureAllowSummaryFallback !== false,
        workflowProfile: nextWorkflow,
        enabledWorkflowModules: nextModules,
      }));
      setCaptureWorkflowMessage("Capture and workflow defaults saved.");
    } catch {
      setCaptureWorkflowMessage("Failed to save capture and workflow defaults.");
    } finally {
      setCaptureWorkflowSaving(false);
    }
  };

  const handleExportAll = async () => {
    setExportAllBusy(true);
    setExportMessage("");
    const fmt = normalizeExportFormat(exportDefaults.format);
    const params = {
      format: fmt,
      include_chat: !!exportDefaults.includeChat,
      include_thoughts: !!exportDefaults.includeThoughts,
      include_tools: !!exportDefaults.includeTools,
    };
    try {
      const res = await axios.get("/api/conversations/export-all", {
        params,
        responseType: "blob",
      });
      const disposition = res.headers?.["content-disposition"] || "";
      let filename = `float-conversations-${new Date()
        .toISOString()
        .replace(/[:.]/g, "")
        .replace("T", "-")
        .replace("Z", "")}.zip`;
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      if (match && match[1]) {
        filename = match[1];
      }
      if (!filename.toLowerCase().endsWith(".zip")) {
        filename = `${filename}.zip`;
      }
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setExportMessage("Exported all conversations.");
    } catch {
      setExportMessage("Export all failed.");
    } finally {
      setExportAllBusy(false);
    }
  };

  const extractSyncError = (err, fallback) => {
    const detail = err?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail.trim();
    }
    return fallback;
  };

  const summarizeSyncSections = (sectionMap) => {
    if (!sectionMap || typeof sectionMap !== "object") return "Sync complete.";
    const parts = Object.entries(sectionMap)
      .map(([key, value]) => {
        if (!value || typeof value !== "object") return null;
        const applied = Number(value.applied || 0);
        const skipped = Number(value.skipped || 0);
        if (!applied && !skipped) return null;
        const label = key.replace(/_/g, " ");
        if (applied && skipped) return `${label}: ${applied} applied, ${skipped} skipped`;
        if (applied) return `${label}: ${applied} applied`;
        return `${label}: ${skipped} skipped`;
      })
      .filter(Boolean);
    return parts.length ? parts.join(" | ") : "Sync complete.";
  };

  const syncPreviewStatusLabel = (status) => {
    const key = String(status || "").trim().toLowerCase();
    if (key === "only_remote") return "Only remote";
    if (key === "only_local") return "Only local";
    if (key === "remote_newer") return "Remote newer";
    if (key === "local_newer") return "Local newer";
    if (key === "identical") return "Identical";
    return key || "Changed";
  };

  const syncPreviewStatusTone = (status) => {
    const key = String(status || "").trim().toLowerCase();
    if (key === "remote_newer" || key === "only_remote") {
      return "color-mix(in oklab, var(--color-mint-green) 18%, transparent)";
    }
    if (key === "local_newer" || key === "only_local") {
      return "color-mix(in oklab, var(--color-lavender) 22%, transparent)";
    }
    return "color-mix(in oklab, var(--color-neutral) 12%, transparent)";
  };

  const renderSyncPreviewItems = (title, items) => {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return null;
    return (
      <div style={{ marginTop: 8 }}>
        <div className="status-note">{title}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {list.map((item) => (
            <span
              key={`${title}-${item.resource_id}-${item.status}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 8px",
                borderRadius: 999,
                border: "1px solid var(--glass-border)",
                background: syncPreviewStatusTone(item.status),
                fontSize: "0.75rem",
                lineHeight: 1.2,
              }}
            >
              <strong>{item.label || item.resource_id}</strong>
              <span>{syncPreviewStatusLabel(item.status)}</span>
            </span>
          ))}
        </div>
      </div>
    );
  };

  const getSyncOptionsPayload = () => {
    const payload = {
      link_to_source: !!syncLinkToSourceDevice,
    };
    const namespace = syncSourceNamespace.trim();
    if (namespace) {
      payload.source_namespace = namespace;
    }
    return payload;
  };

  const previewSync = async () => {
    const remoteUrl = syncRemoteUrl.trim();
    if (!remoteUrl) {
      setSyncMessage("Enter the other Float instance URL first.");
      return;
    }
    setSyncBusy(true);
    setSyncMessage("");
    try {
      const res = await axios.post("/api/sync/plan", {
        remote_url: remoteUrl,
        ...getSyncOptionsPayload(),
      });
      const sections = Array.isArray(res?.data?.pull_sections)
        ? res.data.pull_sections
        : Array.isArray(res?.data?.sections)
          ? res.data.sections
          : [];
      setSyncPreview(res.data || null);
      setSyncSelections(
        sections.reduce((acc, section) => {
          if (section?.key) acc[section.key] = !!section.selected_by_default;
          return acc;
        }, {}),
      );
      setSyncDialogOpen(true);
    } catch (err) {
      setSyncMessage(extractSyncError(err, "Failed to preview instance sync."));
    } finally {
      setSyncBusy(false);
    }
  };

  const applySync = async (direction) => {
    const sections = Object.entries(syncSelections)
      .filter(([, selected]) => !!selected)
      .map(([key]) => key);
    if (!sections.length) {
      setSyncMessage("Choose at least one section to sync.");
      return;
    }
    setSyncActionBusy(direction);
    setSyncMessage("");
    try {
      const res = await axios.post("/api/sync/apply", {
        remote_url: syncRemoteUrl.trim(),
        direction,
        sections,
        ...getSyncOptionsPayload(),
      });
      const sectionMap =
        direction === "push"
          ? res?.data?.result?.sections
          : res?.data?.result?.sections;
      const effectiveNamespace =
        res?.data?.effective_namespace || res?.data?.result?.effective_namespace;
      setSyncMessage(
        direction === "push"
          ? `Push complete. ${summarizeSyncSections(sectionMap)}${
              effectiveNamespace
                ? ` Remote copy linked under ${effectiveNamespace}/.`
                : ""
            }`
          : `Pull complete. ${summarizeSyncSections(sectionMap)}${
              effectiveNamespace ? ` Stored under ${effectiveNamespace}/.` : ""
            }`
      );
      setSyncDialogOpen(false);
    } catch (err) {
      setSyncMessage(
        extractSyncError(
          err,
          direction === "push"
            ? "Failed to push data to the remote Float instance."
            : "Failed to pull data from the remote Float instance."
        )
      );
    } finally {
      setSyncActionBusy("");
    }
  };

  const syncPullSections = Array.isArray(syncPreview?.pull_sections)
    ? syncPreview.pull_sections
    : Array.isArray(syncPreview?.sections)
      ? syncPreview.sections
      : [];
  const syncPushSections = Array.isArray(syncPreview?.push_sections)
    ? syncPreview.push_sections
    : syncPullSections;
  const syncPushSectionMap = syncPushSections.reduce((acc, section) => {
    if (section?.key) {
      acc[section.key] = section;
    }
    return acc;
  }, {});
  const syncPullNamespace =
    typeof syncPreview?.effective_namespaces?.pull === "string"
      ? syncPreview.effective_namespaces.pull
      : "";
  const syncPushNamespace =
    typeof syncPreview?.effective_namespaces?.push === "string"
      ? syncPreview.effective_namespaces.push
      : "";



  return (

    <div className="settings-container">

      <div className="settings-header">

        <h1>Settings</h1>

        <button onClick={handleSave} disabled={saving || !isDirty}>

          {saving ? "Saving..." : "Save"}

        </button>

      </div>

      {!loading && (
        <section className="settings-toolbar-card settings-section" aria-label="Settings navigation">
          <div className="settings-card-header">
            <div>
              <h2>Search settings</h2>
              <p className="settings-card-copy">
                Find settings by keyword, including live streaming, camera, transcript, tools,
                export, and prompt options.
              </p>
            </div>
          </div>
          <div className="settings-search-row">
            <label className="field-label settings-search-label" htmlFor="settings-page-search">
              <span>Search settings</span>
            </label>
            <div className="settings-search-input-row">
              <input
                id="settings-page-search"
                type="search"
                value={settingsSearch}
                onChange={(e) => setSettingsSearch(e.target.value)}
                placeholder="live streaming, api key, gpu, tools, export, prompt..."
              />
              {settingsSearch && (
                <button
                  type="button"
                  className="icon-btn settings-search-clear"
                  onClick={() => setSettingsSearch("")}
                  style={{ marginTop: 0 }}
                >
                  Clear search
                </button>
              )}
            </div>
          </div>
          <div className="settings-chip-row" role="navigation" aria-label="Settings sections">
            {SETTINGS_SECTIONS.map((section) => (
              <a
                key={section.id}
                href={`#settings-${section.id}`}
                className={`settings-chip${
                  showSettingsSection(section.id) ? "" : " settings-chip--muted"
                }`}
                onClick={() => {
                  if (settingsSearchTerms.length) {
                    setSettingsSearch("");
                  }
                }}
              >
                {section.label}
              </a>
            ))}
          </div>
          <p className="status-note" aria-live="polite">
            {settingsSearchTerms.length
              ? `Showing ${visibleSettingsSections.length} of ${SETTINGS_SECTIONS.length} sections for "${settingsSearch.trim()}".`
              : "Try broad terms like live streaming, runtime, model, gpu, notification, tools, export, or prompt."}
          </p>
        </section>
      )}

      {/* Consolidated runtime status indicators */}

      {!loading && <StatusSection />}

      {loading ? (

        loadingView

      ) : (

        <>

          {settingsSearchTerms.length > 0 && visibleSettingsSections.length === 0 && (
            <section className="settings-card settings-section">
              <div className="settings-card-header">
                <div>
                  <h2>No matches</h2>
                  <p className="settings-card-copy">
                    No settings sections match &quot;{settingsSearch.trim()}&quot;. Clear the search or use a
                    broader term.
                  </p>
                </div>
              </div>
            </section>
          )}

          {showSettingsSection("connections") && (
            <section
              id="settings-connections"
              className="settings-card settings-section"
              aria-label="Connections and access"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Connections &amp; Access</h2>
                  <p className="settings-card-copy">
                    External endpoints, secrets, and knowledge base connectivity.
                  </p>
                </div>
              </div>

          <div className="settings-section" style={{ marginBottom: 12 }}>

            <h3>Knowledge Base (Weaviate)</h3>

            <label title="Weaviate base URL (http/https)">Weaviate URL</label>

            <input

              name="weaviate_url"

              type="text"

              value={settings.weaviate_url}

              onChange={handleChange}

              placeholder="http://localhost:8080"

              title="Weaviate base URL (http/https)"

            />

            <div className="inline-flex" style={{ alignItems: 'center', gap: 8, marginTop: 6 }}>

              <button type="button" onClick={refreshWeaviateStatus} disabled={wvLoading}>

                {wvLoading ? 'Checking…' : 'Check Status'}

              </button>

              <span>

                {weaviateStatus.reachable == null ? (

                  <span className="status-badge status-badge--loading">checking</span>

                ) : weaviateStatus.reachable ? (

                  <span className="status-badge status-badge--online">reachable</span>

                ) : (

                  <span className="status-badge status-badge--offline">unreachable</span>

                )}

              </span>

              <button type="button" onClick={handleWeaviateStart} disabled={wvStarting}>

                {wvStarting ? 'Starting…' : 'Start Weaviate'}

              </button>

            </div>

            {wvMessage && (

              <div className="settings-message" role="status" style={{ marginTop: 4 }}>

                {wvMessage}

              </div>

            )}

            <label title="Try to auto‑start Weaviate via Docker when needed">Auto‑start Weaviate</label>

            <input

              name="weaviate_auto_start"

              type="checkbox"

              checked={!!settings.weaviate_auto_start}

              onChange={(e) => setSettings((prev) => ({ ...prev, weaviate_auto_start: !!e.target.checked }))}

              title="Try to auto‑start Weaviate via Docker when needed"

            />

          </div>

          <label title="Secret token for provider APIs (e.g., OpenAI)">API Key</label>

          <div className="secret-input-row">
            <input
              name="api_key"
              type={showApiKey ? "text" : "password"}
              value={settings.api_key}
              onChange={handleChange}
              placeholder={
                settings.api_key_set ? "Stored (not displayed)" : "OPENAI API Key"
              }
              title="Secret token for provider APIs (e.g., OpenAI)"
              autoComplete="new-password"
            />
            <button
              type="button"
              className="secret-toggle-btn"
              onClick={() => setShowApiKey((prev) => !prev)}
              title={showApiKey ? "Hide API key" : "Show API key"}
            >
              {showApiKey ? "hide" : "show"}
            </button>
          </div>
          {showApiKey && !settings.api_key && settings.api_key_preview && (
            <div className="secret-preview">{settings.api_key_preview}</div>
          )}

          <label title="Hugging Face token for gated model downloads">HF Token</label>

          <div className="secret-input-row">
            <input
              name="hf_token"
              type={showHfToken ? "text" : "password"}
              value={settings.hf_token}
              onChange={handleChange}
              placeholder={
                settings.hf_token_set
                  ? "Stored (not displayed)"
                  : "HUGGINGFACE_HUB_TOKEN"
              }
              title="Hugging Face token for gated model downloads"
              autoComplete="new-password"
            />
            <button
              type="button"
              className="secret-toggle-btn"
              onClick={() => setShowHfToken((prev) => !prev)}
              title={showHfToken ? "Hide HF token" : "Show HF token"}
            >
              {showHfToken ? "hide" : "show"}
            </button>
          </div>
          {showHfToken && !settings.hf_token && settings.hf_token_preview && (
            <div className="secret-preview">{settings.hf_token_preview}</div>
          )}
          <div className={`status-note ${settings.hf_token_set ? "" : "warn"}`}>
            {settings.hf_token_set
              ? "HF token stored (hidden)."
              : "HF token not stored yet. Click Save to persist it."}
          </div>

          <label

            className={`field-label${endpointWarning ? " field-label--warn" : ""}`}

            title="Base URL for external API or proxy (optional)"

          >

            <span>External API URL</span>

            {endpointWarning && (

              <span

                className="status-dot warn label-dot"

                title={endpointStatus.message}

                role="img"

                aria-label={endpointStatus.message}

              />

            )}

          </label>

          <input

            name="api_url"

            type="text"

            value={settings.api_url}

            onChange={handleChange}

            placeholder="https://api.example.com"

            title="Base URL for external API or proxy (optional)"

          />

          {endpointWarning && (

            <div className="status-note warn form-note" role="note">

              {endpointStatus.message}

            </div>

          )}

          <label title="Override or point Float at a specific MCP server endpoint">MCP Server URL</label>

          <input

            name="local_url"

            type="text"

            value={settings.local_url}

            onChange={handleChange}

            placeholder="http://127.0.0.1:4000"

            title="Override or point Float at a specific MCP server endpoint"

          />

          <label title="URL for an OpenAI-compatible LLM server (e.g., LM Studio)">LLM Server URL</label>

          <input

            name="server_url"

            type="text"

            value={settings.server_url}

            onChange={handleChange}

            placeholder="http://localhost:11434"

            title="URL for an OpenAI-compatible LLM server (e.g., LM Studio)"

          />

            </section>
          )}

          {showSettingsSection("runtime") && (
            <section
              id="settings-runtime"
              className="settings-card settings-section"
              aria-label="Runtime and provider"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Runtime &amp; Provider</h2>
                  <p className="settings-card-copy">
                    Choose the active runtime, with direct Transformers as the
                    primary local path and provider bridges kept separate.
                  </p>
                </div>
              </div>

          <label title="Choose runtime mode: Cloud API, Local (on-device), or Server/LAN">
            Mode
          </label>

          <select name="mode" value={settings.mode} onChange={handleChange}>

            <option value="api">Cloud API</option>

          <option value="local">Local (on-device)</option>

          <option value="server">Server/LAN</option>

        </select>
          <div className="status-note form-note">Offline = Local (on-device).</div>

        {directLocalRuntimeSelected && availableDevices.length > 0 && (
          <>
            <label title="Select the device used for local inference">
              Inference Device
            </label>
            <select
              name="inference_device"
              value={selectedInferenceId}
              onChange={handleChange}
              title="Select the device used for local inference"
            >
              {availableDevices.map((device, idx) => {
                const identifier =
                  device?.id || device?.name || `device-${idx}`;
                const labelParts = [
                  device?.name || device?.id || `Device ${idx + 1}`,
                  device?.type
                    ? String(device.type).toUpperCase()
                    : null,
                  typeof device?.total_memory_gb === "number" &&
                  Number.isFinite(device.total_memory_gb)
                    ? `${device.total_memory_gb} GB`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const optionValue = device?.id || device?.name || identifier;
                return (
                  <option key={identifier} value={optionValue}>
                    {labelParts}
                  </option>
                );
              })}
            </select>
            {selectedDeviceSummary && (
              <div className="form-note">{selectedDeviceSummary}</div>
            )}
            <div
              className="inline-flex"
              style={{ marginTop: 6, alignItems: "center", gap: 8 }}
            >
              <span className={cudaBadgeClass} title={cudaBadgeTitle}>
                {cudaBadgeLabel}
              </span>
              {cudaBadgeNote ? (
                <span className={`status-note${cudaNoteWarn ? " warn" : ""}`}>
                  {cudaBadgeNote}
                </span>
              ) : null}
            </div>
          </>
        )}

        {settings.mode === "local" && !directLocalRuntimeSelected && (
          <p className="status-note">
            Device and CUDA controls only apply when `Local Language Model` points
            at a direct on-device Transformers checkpoint. The current local
            runtime is routed through the external compatibility bridge{" "}
            {formatLocalRuntimeLabel(settings.transformer_model || selectedProviderKey)}.
          </p>
        )}

          {settings.mode === "api" && (

            <>

              <label title="Provider model used via external API">API Model</label>

              <select

                name="model"

                value={settings.model}

                onChange={handleChange}

                title="Provider model used via external API"

              >

                <optgroup label="defaults">
                  {apiModelGroups.defaults.map((m) => {
                    const disabled =
                      apiModelsAvailableSet.size > 0 &&
                      !apiModelsAvailableSet.has(m);
                    const label = disabled ? `${m} (unavailable)` : m;
                    return (
                      <option key={m} value={m} disabled={disabled}>
                        {label}
                      </option>
                    );
                  })}
                </optgroup>
                {apiModelGroups.extras.length > 0 && (
                  <optgroup
                    label={`available${apiModelsAvailable.length ? ` (${apiModelsAvailable.length})` : ""}`}
                  >
                    {apiModelGroups.extras.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </optgroup>
                )}

              </select>

            </>

          )}

          {settings.mode === "local" && (

            <>
              <label title="Local model alias (Transformers checkpoints or managed runtime marker).">
                Local Language Model
              </label>

              <select

                name="transformer_model"

                value={settings.transformer_model}

                onChange={handleChange}

                title="Local language model alias"

              >

                {suggestedLangModels.map((m) => (

                  <option key={m} value={m}>

                    {isLocalRuntimeEntry(m) ? formatLocalRuntimeLabel(m) : m}

                  </option>

                ))}

                {settings.transformer_model && !suggestedLangModels.includes(settings.transformer_model) && (

                  <option value={settings.transformer_model}>
                    {isLocalRuntimeEntry(settings.transformer_model)
                      ? formatLocalRuntimeLabel(settings.transformer_model)
                      : settings.transformer_model}
                  </option>

                )}

              </select>

              <p className="status-note">
                Direct Transformers checkpoints are the primary local runtime path.
                `local/lmstudio` and `local/ollama` stay here as external
                compatibility bridges, not the main runtime target.
              </p>

              <details className="advanced-block mt-sm">
                <summary>External provider compatibility (LM Studio / Ollama)</summary>

                <label title="Select the external provider bridge used when the local model points at a provider marker.">
                  External Provider
                </label>

                <select
                  name="local_provider"
                  value={settings.local_provider || "lmstudio"}
                  onChange={handleChange}
                  title="These adapters exist for compatibility checks and external runtimes."
                >
                  <option value="lmstudio">LM Studio</option>
                  <option value="ollama">Ollama</option>
                  <option value="custom-openai-compatible">
                    Custom OpenAI-compatible
                  </option>
                </select>

                <label title="Choose whether Float may manage a local compatibility server or only call an external HTTP endpoint.">
                  Provider Mode
                </label>

                <select
                  name="local_provider_mode"
                  value={settings.local_provider_mode || "local-managed"}
                  onChange={handleChange}
                  title="Remote unmanaged mode never tries to start or stop remote processes."
                >
                  <option value="local-managed">Local managed bridge</option>
                  <option value="remote-unmanaged">External HTTP only</option>
                </select>

                <div className="inline-flex" style={{ gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <label title="Provider host for local-managed or remote-unmanaged mode.">Provider Host</label>
                  <input
                    name="local_provider_host"
                    value={settings.local_provider_host || ""}
                    onChange={handleChange}
                    placeholder="127.0.0.1"
                  />
                  <label title="Provider port. Defaults are 1234 (LM Studio) and 11434 (Ollama).">Provider Port</label>
                  <input
                    name="local_provider_port"
                    type="number"
                    min="1"
                    step="1"
                    value={settings.local_provider_port ?? ""}
                    onChange={handleChange}
                    placeholder={settings.local_provider === "ollama" ? "11434" : "1234"}
                  />
                </div>

                <label title="Optional explicit base URL for provider HTTP API.">
                  Provider Base URL
                </label>
                <input
                  name="local_provider_base_url"
                  value={settings.local_provider_base_url || ""}
                  onChange={handleChange}
                  placeholder="http://127.0.0.1:1234/v1"
                />

                <label title="Path to LM Studio CLI binary (lms). Leave empty if it is already on PATH.">
                  LM Studio CLI Path
                </label>
                <input
                  name="lmstudio_path"
                  value={settings.lmstudio_path || ""}
                  onChange={handleChange}
                  placeholder="C:\\Program Files\\LM Studio\\lms.exe"
                />

                <label title="Optional provider API token used for OpenAI-compatible requests.">
                  Provider API Token
                </label>
                <input
                  name="local_provider_api_token"
                  type="password"
                  value={settings.local_provider_api_token || ""}
                  onChange={handleChange}
                  placeholder={
                    settings.local_provider_api_token_set
                      ? "Stored (not displayed)"
                      : "Provider API token (optional)"
                  }
                />
                {!settings.local_provider_api_token &&
                  settings.local_provider_api_token_preview && (
                    <div className="secret-preview">
                      {settings.local_provider_api_token_preview}
                    </div>
                  )}

                <div className="inline-flex" style={{ gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <label className="field-label" title="Auto-start provider server when needed in local-managed mode.">
                    <input
                      type="checkbox"
                      name="local_provider_auto_start"
                      checked={!!settings.local_provider_auto_start}
                      onChange={handleChange}
                    />
                    <span style={{ marginLeft: 6 }}>Auto-start bridge</span>
                  </label>
                  <label className="field-label" title="Show provider server logs in the runtime panel.">
                    <input
                      type="checkbox"
                      name="local_provider_show_server_logs"
                      checked={!!settings.local_provider_show_server_logs}
                      onChange={handleChange}
                    />
                    <span style={{ marginLeft: 6 }}>Show provider logs</span>
                  </label>
                  <label className="field-label" title="Enable CORS when starting LM Studio from Float.">
                    <input
                      type="checkbox"
                      name="local_provider_enable_cors"
                      checked={!!settings.local_provider_enable_cors}
                      onChange={handleChange}
                    />
                    <span style={{ marginLeft: 6 }}>Enable CORS</span>
                  </label>
                  <label className="field-label" title="Allow LAN access when starting LM Studio from Float.">
                    <input
                      type="checkbox"
                      name="local_provider_allow_lan"
                      checked={!!settings.local_provider_allow_lan}
                      onChange={handleChange}
                    />
                    <span style={{ marginLeft: 6 }}>Allow LAN</span>
                  </label>
                </div>

                <div className="settings-section" style={{ marginTop: 12 }}>
                  <div className="status-header">
                    <div>
                      <strong>Provider bridge runtime</strong>
                      <div className="status-sub">
                        {selectedProviderKey}
                        {providerRuntime?.base_url ? ` • ${providerRuntime.base_url}` : ""}
                      </div>
                    </div>
                    {renderStatusBadge(providerRuntimeStatus)}
                  </div>
                  <p className="status-note" style={{ marginTop: 6 }}>
                    {providerRuntimeSummary}
                  </p>
                  <p className={`status-note${providerRuntimeError ? " warn" : ""}`}>
                    {providerRuntimeDetail}
                  </p>
                  {providerRuntime?.context_length ? (
                    <p className="status-note">
                      Active context length: {providerRuntime.context_length}
                    </p>
                  ) : null}
                  <div className="inline-flex" style={{ gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => refreshProviderRuntime()}
                      disabled={providerRuntimeLoading || !providerRuntimeInspectable}
                      style={{ marginTop: 0 }}
                    >
                      {providerRuntimeLoading ? "Refreshing..." : "Refresh"}
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => runProviderAction("start")}
                      disabled={!providerRuntimeInspectable || !!providerActionBusy}
                      style={{ marginTop: 0 }}
                    >
                      {providerActionBusy === "start" ? "Starting..." : "Start"}
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => runProviderAction("stop")}
                      disabled={!providerRuntimeInspectable || !!providerActionBusy}
                      style={{ marginTop: 0 }}
                    >
                      {providerActionBusy === "stop" ? "Stopping..." : "Stop"}
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => runProviderAction("load")}
                      disabled={!providerRuntimeInspectable || !!providerActionBusy}
                      style={{ marginTop: 0 }}
                    >
                      {providerActionBusy === "load" ? "Loading..." : "Load preferred"}
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => runProviderAction("unload")}
                      disabled={!providerRuntimeInspectable || !!providerActionBusy}
                      style={{ marginTop: 0 }}
                    >
                      {providerActionBusy === "unload" ? "Unloading..." : "Unload"}
                    </button>
                  </div>
                  {providerActionMessage ? (
                    <p className="status-note" style={{ marginTop: 8 }}>
                      {providerActionMessage}
                    </p>
                  ) : null}
                </div>

                <div className="inline-flex" style={{ gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <label title="Preferred model for provider load actions when no model is selected in the runtime panel.">
                    Preferred Provider Model
                  </label>
                  <input
                    name="local_provider_preferred_model"
                    value={settings.local_provider_preferred_model || ""}
                    onChange={handleChange}
                    placeholder={providerModelOptions[0] || "gpt-oss-20b"}
                    list={
                      providerRuntimeInspectable && providerModelOptions.length > 0
                        ? "provider-model-options"
                        : undefined
                    }
                  />
                  {providerRuntimeInspectable && providerModelOptions.length > 0 && (
                    <datalist id="provider-model-options">
                      {providerModelOptions.map((model) => (
                        <option key={model} value={model} />
                      ))}
                    </datalist>
                  )}
                  <label title="Default context length for provider load actions (optional).">
                    Provider Context Length
                  </label>
                  <input
                    name="local_provider_default_context_length"
                    type="number"
                    min="0"
                    step="1"
                    value={settings.local_provider_default_context_length ?? ""}
                    onChange={handleChange}
                    placeholder="0"
                  />
                </div>
                <p className="status-note">
                  Use this bridge only when you intentionally want Float to defer
                  execution to an external LM Studio or Ollama runtime.
                </p>
              </details>

              {/* Deprecated: dynamic server port */}

            </>

          )}

          <div className="inline-flex" style={{ gap: 12, alignItems: "center", marginTop: 12 }}>
            <label title="Max seconds to wait on a request before retry/fail.">Request Timeout (s)</label>
            <input
              name="request_timeout"
              type="number"
              min="1"
              step="1"
              value={settings.request_timeout ?? ""}
              onChange={handleChange}
              placeholder="30"
            />
            <label title="Max idle seconds while streaming before aborting.">Stream Idle Timeout (s)</label>
            <input
              name="stream_idle_timeout"
              type="number"
              min="1"
              step="1"
              value={settings.stream_idle_timeout ?? ""}
              onChange={handleChange}
              placeholder="120"
            />
          </div>

            </section>
          )}

          {showSettingsSection("models") && (
            <section
              id="settings-models"
              className="settings-card settings-section"
              aria-label="Models and retrieval"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Models &amp; Retrieval</h2>
                  <p className="settings-card-copy">
                    Model defaults, local aliases, retrieval behavior, and vision/audio options.
                  </p>
                </div>
              </div>

          <h3 className="settings-subsection-title">Model library</h3>
          <div className="inline-flex" style={{ justifyContent: "flex-end", marginTop: -6 }}>
            <button type="button" className="icon-btn" onClick={openDownloadsTray}>
              Downloads
            </button>
          </div>
          <label className="field-label" title="Include every Hugging Face cache entry, even tiny utility models.">
            <input
              type="checkbox"
              checked={includeCacheUnfiltered}
              onChange={(e) => {
                const next = e.target.checked;
                setIncludeCacheUnfiltered(next);
                fetchAvailableModels(
                  useCustomModelsFolder ? settings.models_folder : undefined,
                  next,
                );
              }}
            />
            <span style={{ marginLeft: 6 }}>Show all HF cache models (may include utility/noisy entries)</span>
          </label>
          {useCustomModelsFolder ? (
            <>
              <label title="Register a local model file/folder by alias so it appears in model pickers.">
                Register Local Model Path
                <span
                  className="hint-badge"
                  title="Maps a local checkpoint path to an alias, then injects that alias into model dropdowns by type."
                >
                  ?
                </span>
              </label>
              <div className="model-register-row">
                <input
                  type="text"
                  value={registerModelAlias}
                  onChange={(e) => setRegisterModelAlias(e.target.value)}
                  placeholder="Alias (optional)"
                  title="Alias used in dropdowns. Defaults to folder/file name."
                />
                <input
                  type="text"
                  value={registerModelPath}
                  onChange={(e) => setRegisterModelPath(e.target.value)}
                  placeholder="Local path (absolute or repo-relative)"
                  title="Path to an existing local model file or folder."
                />
                <select
                  value={registerModelType}
                  onChange={(e) => setRegisterModelType(e.target.value)}
                  title="Model type for type-aware dropdown placement."
                >
                  <option value="transformer">Language</option>
                  <option value="stt">Speech-to-text</option>
                  <option value="tts">Text-to-speech</option>
                  <option value="vision">Vision</option>
                  <option value="voice">Voice</option>
                  <option value="other">Other</option>
                </select>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={handleRegisterLocalModel}
                  disabled={registerModelBusy}
                  title="Register local model path"
                >
                  + Add
                </button>
              </div>
              {registerModelMessage && (
                <div className="settings-message model-register-message" role="status">
                  {registerModelMessage}
                </div>
              )}
              {registeredLocalModels.length > 0 && (
                <div className="model-register-list">
                  {registeredLocalModels.map((entry) => {
                    const alias = String(entry?.alias || "").trim();
                    const path = String(entry?.path || "").trim();
                    const modelType = String(entry?.model_type || "other").trim();
                    const exists = entry?.exists !== false;
                    if (!alias || !path) return null;
                    return (
                      <div
                        key={`${alias}:${path}`}
                        className={`model-register-item${exists ? "" : " missing"}`}
                      >
                        <span className="model-register-item-main">{alias}</span>
                        <span className="model-register-item-type">{modelType}</span>
                        <span className="model-register-item-path" title={path}>
                          {path}
                        </span>
                        <button
                          type="button"
                          className="icon-btn"
                          onClick={() => handleUnregisterLocalModel(alias)}
                          disabled={registerModelBusy}
                          title="Remove registered model alias"
                        >
                          Remove
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          ) : (
            <div className="status-note">
              Enable <code>Use Custom Models Folder</code> to register local model paths.
            </div>
          )}

          {renderModelField(

            "Language Model",

            "transformer_model",

            suggestedLangModels,

          )}

          <label

            className={`field-label${harmonyWarning ? " field-label--warn" : ""}`}

            title="Format responses using Harmony metadata when supported"

          >

            <span>Harmony Formatting</span>

            {harmonyWarning && (

              <span

                className="status-dot warn label-dot"

                title={harmonyWarningMessage}

                role="img"

                aria-label={harmonyWarningMessage}

              />

            )}

          </label>

          <input

            name="harmony_format"

            type="checkbox"

            checked={settings.harmony_format}

            onChange={handleChange}

            title="Format responses using Harmony metadata when supported"

          />

          {harmonyWarning && harmonyWarningMessage && (

            <div className="status-note warn form-note" role="note">

              {harmonyWarningMessage}

            </div>

          )}

          {renderModelField(

            "STT Model",

            "stt_model",

            suggestedSttModels,

          )}

          {renderModelField(

            "TTS Model",

            "tts_model",

            suggestedTtsModels,
            voicePresetInput,

          )}

          <div className="settings-section">
            <h3>Live streaming</h3>
            <label title={fieldTooltips.stream_backend}>
              Streaming backend
            </label>
            <select
              name="stream_backend"
              value={settings.stream_backend || "api"}
              onChange={handleChange}
              title={fieldTooltips.stream_backend}
            >
              <option value="api">OpenAI Realtime (WebRTC)</option>
              <option value="livekit">LiveKit room</option>
            </select>

            <label title={fieldTooltips.realtime_model}>
              Realtime model
            </label>
            <input
              name="realtime_model"
              value={settings.realtime_model || ""}
              onChange={handleChange}
              list="realtime-model-options"
              placeholder="gpt-realtime"
              title={fieldTooltips.realtime_model}
            />
            <datalist id="realtime-model-options">
              {realtimeModelOptions.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>

            <label title={fieldTooltips.realtime_voice}>
              Realtime voice
            </label>
            <select
              name="realtime_voice"
              value={settings.realtime_voice || ""}
              onChange={handleChange}
              title={fieldTooltips.realtime_voice}
            >
              {realtimeVoiceOptions.map((voice) => (
                <option key={voice} value={voice}>
                  {voice}
                </option>
              ))}
              {settings.realtime_voice &&
                !realtimeVoiceOptions.includes(settings.realtime_voice) && (
                  <option value={settings.realtime_voice}>
                    {settings.realtime_voice}
                  </option>
                )}
            </select>

            <label title="Endpoint used by the backend to mint short-lived OpenAI Realtime client secrets.">
              Realtime session URL
            </label>
            <input
              name="realtime_base_url"
              value={settings.realtime_base_url || ""}
              onChange={handleChange}
              placeholder="https://api.openai.com/v1/realtime/client_secrets"
              title="Endpoint used by the backend to mint short-lived OpenAI Realtime client secrets."
            />

            <label title="Endpoint the browser uses for the OpenAI Realtime WebRTC SDP exchange.">
              Realtime connect URL
            </label>
            <input
              name="realtime_connect_url"
              value={settings.realtime_connect_url || ""}
              onChange={handleChange}
              placeholder="https://api.openai.com/v1/realtime/calls"
              title="Endpoint the browser uses for the OpenAI Realtime WebRTC SDP exchange."
            />

            <label title="Show the current live transcript inside chat while live streaming mode is active.">
              Show live transcript
            </label>
            <input
              type="checkbox"
              checked={state.liveTranscriptEnabled !== false}
              onChange={(event) =>
                setState((prev) => ({
                  ...prev,
                  liveTranscriptEnabled: event.target.checked,
                }))
              }
              title="Show the current live transcript inside chat while live streaming mode is active."
            />

            <label title="Start the camera automatically when live streaming mode begins.">
              Start camera automatically
            </label>
            <input
              type="checkbox"
              checked={state.liveCameraDefaultEnabled === true}
              onChange={(event) =>
                setState((prev) => ({
                  ...prev,
                  liveCameraDefaultEnabled: event.target.checked,
                }))
              }
              title="Start the camera automatically when live streaming mode begins."
            />

            <p className="status-note form-note">
              OpenAI Realtime uses a short-lived client secret from the backend and
              connects from the browser over WebRTC. The TTS voice field above is separate
              and only affects speech synthesis, not live streaming mode.
            </p>
            <p className="status-note form-note">
              Use LiveKit only when you are intentionally running a LiveKit server. Leave
              the OpenAI URLs at their defaults unless you are targeting a compatible proxy.
            </p>
            {settings.stream_backend === "livekit" && (
              <p className="status-note form-note">
                OpenAI Realtime model and URL fields are ignored while LiveKit is selected.
              </p>
            )}
            <p className="status-note form-note">
              The transcript and camera-start toggles are UI preferences and save
              automatically; backend and model changes still use the main Save button.
            </p>
          </div>

          <label htmlFor="rag-embedding-model" title="Text embedding model used for semantic search (RAG).">

            RAG embedding model

          </label>

          <div className="model-select-row model-present">
            <select
              id="rag-embedding-model"
              name="rag_embedding_model"
              value={settings.rag_embedding_model || ""}
              onChange={handleChange}
              title="Text embedding model used for semantic search (RAG)."
            >
              {ragEmbeddingPresets.map((preset) => (
                <option key={preset.value} value={preset.value}>
                  {preset.label}
                </option>
              ))}
              {settings.rag_embedding_model &&
                !ragEmbeddingPresets.some(
                  (preset) => preset.value === settings.rag_embedding_model,
                ) && (
                  <option value={settings.rag_embedding_model}>
                    {settings.rag_embedding_model}
                  </option>
                )}
            </select>
          </div>

          <p className="status-note">

            Values starting with <code>local:</code> attempt to use on-device embeddings;{" "}

            <code>api:</code> entries are stubbed until remote providers are wired up and currently fall back to the hash-based encoder.

          </p>

          <label htmlFor="rag-clip-model" title="CLIP model used for image-aware RAG retrieval.">
            RAG CLIP model
          </label>

          <div className="model-select-row model-present">
            <select
              id="rag-clip-model"
              name="rag_clip_model"
              value={settings.rag_clip_model || ""}
              onChange={handleChange}
              title="CLIP model used for image-aware RAG retrieval."
            >
              {ragClipPresets.map((preset) => (
                <option key={preset.value} value={preset.value}>
                  {preset.label}
                </option>
              ))}
              {settings.rag_clip_model &&
                !ragClipPresets.some((preset) => preset.value === settings.rag_clip_model) && (
                  <option value={settings.rag_clip_model}>{settings.rag_clip_model}</option>
                )}
            </select>
          </div>

          <p className="status-note">
            Keep this on an OpenCLIP variant; Vision model selection is separate.
          </p>

          <label
            htmlFor="rag-chat-min-similarity"
            title="Minimum similarity (0-1) for automatic RAG injection."
          >
            RAG min similarity
          </label>

          <input
            id="rag-chat-min-similarity"
            name="rag_chat_min_similarity"
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={settings.rag_chat_min_similarity}
            onChange={handleChange}
          />

          <p className="status-note">
            Lower values include more matches; set to 0 to disable similarity filtering.
          </p>

          <details className="advanced-block mt-sm">
            <summary>Experimental SAE steering (stub)</summary>
            <div className="advanced-grid">
              <label title="Planned retrieval/clustering path for threads when SAE hooks are available.">
                Threads signal path
              </label>
              <select
                name="sae_threads_signal_mode"
                value={settings.sae_threads_signal_mode || "hybrid"}
                onChange={handleChange}
              >
                <option value="embeddings">embeddings only (current stable)</option>
                <option value="hybrid">hybrid: SAE core + embeddings fallback</option>
                <option value="sae">SAE only</option>
              </select>

              <label title="Hybrid blend factor for manual thread-label assignment scoring. 0 = embeddings only, 1 = SAE proxy only.">
                SAE hybrid blend
              </label>
              <input
                name="sae_threads_signal_blend"
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={settings.sae_threads_signal_blend ?? 0.7}
                onChange={handleChange}
              />

              <label title="Pre-approved model+SAE combo (or custom).">
                Model + SAE combo
              </label>
              <input
                name="sae_model_combo"
                value={settings.sae_model_combo || ""}
                onChange={handleChange}
                list="settings-sae-combo-presets"
                placeholder="openai/gpt-oss-20b :: future SAE pack"
              />
              <datalist id="settings-sae-combo-presets">
                <option value="openai/gpt-oss-20b :: future SAE pack" />
                <option value="google/gemma-2-2b :: Gemma Scope" />
                <option value="custom" />
              </datalist>

              <label title="Keep embeddings available when SAE path is unsupported on the current runtime/GPU.">
                Embeddings fallback
              </label>
              <input
                name="sae_embeddings_fallback"
                type="checkbox"
                checked={!!settings.sae_embeddings_fallback}
                onChange={handleChange}
              />

              <label title="Enable global SAE steering defaults (metadata/stub until live hooks are enabled).">
                Enable SAE steering
              </label>
              <input
                name="sae_steering_enabled"
                type="checkbox"
                checked={!!settings.sae_steering_enabled}
                onChange={handleChange}
              />

              <label title="Default steering layer index for runtime hook paths.">
                SAE steering layer
              </label>
              <input
                name="sae_steering_layer"
                type="number"
                min="0"
                step="1"
                value={settings.sae_steering_layer ?? 12}
                onChange={handleChange}
              />

              <label title="Default token positions for steering (e.g. all, last, or indexes).">
                SAE steering token positions
              </label>
              <input
                name="sae_steering_token_positions"
                value={settings.sae_steering_token_positions || "last"}
                onChange={handleChange}
                placeholder="last"
              />

              <label title="Feature steering map in feature_id:alpha format.">
                SAE steering features
              </label>
              <input
                name="sae_steering_features"
                value={settings.sae_steering_features || ""}
                onChange={handleChange}
                placeholder="123:+0.8,91:-0.4"
              />

              <label title="Record intended steering without applying hidden-state interventions.">
                SAE steering dry-run
              </label>
              <input
                name="sae_steering_dry_run"
                type="checkbox"
                checked={!!settings.sae_steering_dry_run}
                onChange={handleChange}
              />

              <label title="Stub toggle for future Agent Console live SAE inspection stream.">
                Live inspect in Agent Console
              </label>
              <input
                name="sae_live_inspect_console"
                type="checkbox"
                checked={!!settings.sae_live_inspect_console}
                onChange={handleChange}
              />
            </div>
            <p className="status-note">
              These controls are scaffolding defaults. They are persisted now, while live SAE
              intervention remains runtime-dependent.
            </p>
          </details>

          {renderModelField(

            "Vision Fallback Model",

            "vision_model",

            suggestedVisionModels,

          )}

            </section>
          )}

          {showSettingsSection("performance") && (
            <section
              id="settings-performance"
              className="settings-card settings-section"
              aria-label="Performance and storage"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Performance &amp; Storage</h2>
                  <p className="settings-card-copy">
                    Tune context, hardware budgets, folders, and approval defaults.
                  </p>
                </div>
              </div>

          <label title="Max tokens for local transformers (affects VRAM)">

            Context Length ({settings.context_length})

          </label>

          <input

            name="context_length"

            type="range"

            min="512"

            max="32768"

            step="512"

            value={settings.context_length}

            onChange={handleChange}

            title="Max tokens for local transformers (affects VRAM)"

          />

          <div className="mb-sm">VRAM Estimate: {vramEstimate.toFixed(1)} MB</div>

          <label title="Cache attention keys/values to speed up generation">

            Enable K/V Cache

          </label>

          <input

            name="kv_cache"

            type="checkbox"

            checked={settings.kv_cache}

            onChange={handleChange}

            title="Cache attention keys/values to speed up generation"

          />

          <label title="Allow model to spill to system RAM when VRAM is low">

            Enable RAM Swap

          </label>

          <input

            name="ram_swap"

            type="checkbox"

            checked={settings.ram_swap}

            onChange={handleChange}

            title="Allow model to spill to system RAM when VRAM is low"

          />

          <details className="advanced-block mt-sm">
            <summary>Advanced Local Inference</summary>
            <div className="advanced-grid">
              <label title="Accelerate/transformers device_map hint">
                Device Map Strategy
              </label>
              <select
                name="device_map_strategy"
                value={settings.device_map_strategy || "auto"}
                onChange={handleChange}
                title="Influence how layers are distributed across devices"
              >
                <option value="auto">Auto (Accelerate)</option>
                <option value="balanced_low_0">Balanced (Prefer GPU 0)</option>
                <option value="balanced_high_0">Balanced High (GPU 0)</option>
                <option value="balanced">Balanced (All GPUs)</option>
                <option value="sequential">Sequential</option>
                <option value="cuda:0">Force cuda:0</option>
                <option value="cpu">Force CPU</option>
              </select>

              <label title="Fraction of GPU VRAM allocated to model weights">
                GPU Memory Budget
                {gpuBudgetGb !== null && gpuTotalGb !== null && (
                  <span>
                    {" "}
                    {gpuBudgetGb} GB / {gpuTotalGb} GB ({gpuFractionPercent}
                    %)
                  </span>
                )}
              </label>
              <input
                name="gpu_memory_fraction"
                type="range"
                min="0.2"
                max="1"
                step="0.05"
                value={
                  typeof settings.gpu_memory_fraction === "number"
                    ? settings.gpu_memory_fraction
                    : 0.9
                }
                onChange={handleChange}
                title="Fraction of GPU VRAM reserved for model parameters"
              />

              <label title="Keep this many megabytes of VRAM free after loading">
                GPU Memory Guard (MB)
              </label>
              <input
                name="gpu_memory_margin_mb"
                type="number"
                min="0"
                step="64"
                value={settings.gpu_memory_margin_mb ?? 512}
                onChange={handleChange}
              />

              <label title="Optional hard limit for GPU usage in gigabytes">
                GPU Hard Limit (GB)
              </label>
              <input
                name="gpu_memory_limit_gb"
                type="number"
                min="0"
                step="0.5"
                value={settings.gpu_memory_limit_gb ?? 0}
                onChange={handleChange}
              />

              <label title="Percent of the model permitted to offload to system RAM">
                CPU Offload Fraction
              </label>
              <input
                name="cpu_offload_fraction"
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={settings.cpu_offload_fraction ?? 0.85}
                onChange={handleChange}
              />

              <label title="Upper bound for RAM offload usage in gigabytes">
                CPU Offload Limit (GB)
              </label>
              <input
                name="cpu_offload_limit_gb"
                type="number"
                min="0"
                step="1"
                value={settings.cpu_offload_limit_gb ?? 0}
                onChange={handleChange}
              />

              <label title="Attempt to enable Flash Attention when dependencies exist">
                Enable Flash Attention
              </label>
              <input
                name="flash_attention"
                type="checkbox"
                checked={!!settings.flash_attention}
                onChange={handleChange}
              />

              <label title="Override the attention backend used during inference">
                Attention Implementation Override
              </label>
              <select
                name="attention_implementation"
                value={settings.attention_implementation || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="flash_attention_2">flash_attention_2</option>
                <option value="sdpa">sdpa</option>
                <option value="eager">eager</option>
              </select>

              <label title="Transformers KV cache implementation preference">
                KV Cache Implementation
              </label>
              <select
                name="kv_cache_implementation"
                value={settings.kv_cache_implementation || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="static">static</option>
                <option value="offloaded_static">offloaded_static</option>
                <option value="hybrid">hybrid</option>
                <option value="offloaded_hybrid">offloaded_hybrid</option>
                <option value="hybrid_chunked">hybrid_chunked</option>
                <option value="offloaded">offloaded</option>
                <option value="sliding_window">sliding_window</option>
                <option value="quantized">quantized</option>
              </select>

              <label title="Quantization backend to use when cache implementation is quantized">
                KV Cache Quant Backend
              </label>
              <select
                name="kv_cache_quant_backend"
                value={settings.kv_cache_quant_backend || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="quanto">quanto</option>
                <option value="HQQ">HQQ</option>
              </select>

              <label title="Data type for key/value cache tensors">
                KV Cache DType
              </label>
              <select
                name="kv_cache_dtype"
                value={settings.kv_cache_dtype || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="float16">float16</option>
                <option value="bfloat16">bfloat16</option>
                <option value="float32">float32</option>
                <option value="int8">int8</option>
              </select>

              <label title="Device to prefer for KV cache storage">
                KV Cache Device
              </label>
              <select
                name="kv_cache_device"
                value={settings.kv_cache_device || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="cuda">cuda</option>
                <option value="cpu">cpu</option>
              </select>

              <label title="Override the dtype used when loading model weights">
                Model Weight DType
              </label>
              <select
                name="model_dtype"
                value={settings.model_dtype || ""}
                onChange={handleChange}
              >
                <option value="">Auto</option>
                <option value="float16">float16</option>
                <option value="bfloat16">bfloat16</option>
                <option value="float32">float32</option>
              </select>

              <label title="Limit Torch CPU worker threads (0 keeps default)">
                CPU Thread Count
              </label>
              <input
                name="cpu_thread_count"
                type="number"
                min="0"
                step="1"
                value={settings.cpu_thread_count ?? 0}
                onChange={handleChange}
              />
            </div>
          </details>

          <label title="Use an explicit models directory instead of default (Default: ./models)">

            Use Custom Models Folder

          </label>

          <input

            name="use_custom_models_folder"

            type="checkbox"

            checked={useCustomModelsFolder}

            onChange={(e) => setUseCustomModelsFolder(!!e.target.checked)}

            title="Use an explicit models directory instead of default (Default: ./models)"

          />

          {useCustomModelsFolder && (

            <>

              <label title="Where local models are stored/cached">

                Custom Models Folder

              </label>

              <div className="settings-folder">

                <input

                  name="models_folder"

                  type="text"

                  value={settings.models_folder}

                  onChange={handleChange}

                  placeholder={getServerPathExample("models") || "/path/to/models"}

                  title="Where local models are stored/cached"

                />

                <button type="button" onClick={handleModelsBrowse}>

                  Browse

                </button>

              </div>

            </>

          )}

          <label title="Use an explicit conversations directory instead of default (Default: ./data/conversations)">

            Use Custom Conversations Folder

          </label>

          <input

            name="use_custom_conv_folder"

            type="checkbox"

            checked={useCustomConvFolder}

            onChange={(e) => setUseCustomConvFolder(!!e.target.checked)}

            title="Use an explicit conversations directory instead of default (Default: ./data/conversations)"

          />

          {useCustomConvFolder && (

            <>

              <label title="Where chats are saved on disk">

                Conversations Folder

              </label>

              <div className="settings-folder">

                <input

                  name="conv_folder"

                  type="text"

                  value={settings.conv_folder}

                  onChange={handleChange}

                  placeholder={getServerPathExample("conversations") || "./data/conversations"}

                  title="Where chats are saved on disk"

                />

                <button type="button" onClick={handleBrowse}>

                  Browse

                </button>

              </div>

            </>

          )}

          <label title="Require confirmation for automated actions">

            Approval Level

          </label>

          <select

            name="approvalLevel"

            value={settings.approvalLevel}

            onChange={handleChange}

            title="Require confirmation for automated actions"

          >

            <option value="all">All</option>

            <option value="high">High Risk Only</option>

            <option value="auto">Full Auto</option>

          </select>

            </section>
          )}

          {showSettingsSection("workspace") && (
            <section
              id="settings-workspace"
              className="settings-card settings-section"
              aria-label="Workspace and tools"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Workspace &amp; Tools</h2>
                  <p className="settings-card-copy">
                    Notification behavior, tool presentation, and the built-in tool browser.
                  </p>
                </div>
              </div>

          <div className="settings-section">

            <h3>Notifications</h3>

            {!pushAvailable ? (

              <p>Push is not configured on the server.</p>

            ) : (

              <div className="inline-flex">

                <label title="Send notification this many minutes before event">

                  Calendar notify (minutes before):

                </label>

                <input

                  type="number"

                  min={0}

                  value={notifyMinutes}

                  onChange={(e) =>

                    setNotifyMinutes(parseInt(e.target.value || "0", 10))

                  }

                  title="Send notification this many minutes before event"

                />

                {!pushEnabled ? (

                  <button

                    type="button"

                    onClick={async () => {

                      try {

                        await registerPush({

                          calendarNotifyMinutes: notifyMinutes,

                        });

                        setPushEnabled(true);

                      } catch (e) {

                        alert(String(e));

                      }

                    }}

                  >

                    Enable Push

                  </button>

                ) : (

                  <button

                    type="button"

                    onClick={async () => {

                      try {

                        await unregisterPush();

                        setPushEnabled(false);

                      } catch (e) {

                        alert(String(e));

                      }

                    }}

                  >

                    Disable Push

                  </button>

                )}

              </div>

            )}

            <label
              className="inline-flex"
              title="Notify when a proposed tool is waiting for your review."
            >
              <input
                type="checkbox"
                checked={toolResolutionNotifications}
                onChange={handleToolResolutionNotificationsChange}
              />
              <span>Notify when tools need review</span>
            </label>
            <p className="settings-card-copy">
              Uses the same notification pipeline as push and OS alerts when available.
            </p>
            {String(state.approvalLevel || "").toLowerCase() === "auto" && (
              <p className="status-note warn form-note" role="note">
                Automatic approval skips tool review alerts, so this setting only
                takes effect when approval checks are enabled.
              </p>
            )}
            {notificationPrefMessage && (
              <p className="settings-message" role="status">
                {notificationPrefMessage}
              </p>
            )}

          </div>

          <div className="settings-section">
            <h3>Appearance</h3>
            <label
              className="field-label"
              htmlFor="visual-theme"
              title="Choose the app's color palette family while keeping the existing dark/light toggle."
            >
              Visual theme
            </label>
            <select
              id="visual-theme"
              value={normalizeVisualTheme(state.visualTheme)}
              onChange={(event) =>
                setState((prev) => ({
                  ...prev,
                  visualTheme: normalizeVisualTheme(event.target.value),
                }))
              }
            >
              {VISUAL_THEME_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="status-note" style={{ marginTop: 6 }}>
              Dark and light mode still toggle from the top bar; this picker changes the color family
              underneath them.
            </p>
          </div>

          <div className="settings-section">
            <h3>Tool display</h3>
            <label
              className="field-label"
              htmlFor="tool-display-mode"
              title="Where tool details appear during chat."
            >
              Where tool details appear
            </label>
            <select
              id="tool-display-mode"
              value={normalizeToolDisplayMode(state.toolDisplayMode)}
              onChange={(e) =>
                setState((prev) => ({
                  ...prev,
                  toolDisplayMode: e.target.value,
                }))
              }
            >
              <option value="console">Agent console</option>
              <option value="inline">Inline in chat</option>
              <option value="both">Both</option>
              <option value="auto">Auto</option>
            </select>
            <label
              className="field-label"
              htmlFor="tool-link-behavior"
              title="What happens when you click a tool link inside chat text."
            >
              When a tool link is clicked in chat
            </label>
            <select
              id="tool-link-behavior"
              value={state.toolLinkBehavior === "inline" ? "inline" : "console"}
              onChange={(e) =>
                setState((prev) => ({
                  ...prev,
                  toolLinkBehavior: e.target.value,
                }))
              }
            >
              <option value="console">Focus agent console</option>
              <option value="inline">Expand inline tool card</option>
            </select>
            <p className="status-note" style={{ marginTop: 6 }}>
              Agent console keeps tool details out of the transcript. Inline in chat shows tool
              cards under the related message. Both keeps inline cards visible while the agent
              console still shows the full tool timeline. Auto keeps tool details inline for the
              selected or highlighted message, and while the current response is streaming.
            </p>
            <p className="status-note" style={{ marginTop: 6 }}>
              {(() => {
                const toolDisplayMode = normalizeToolDisplayMode(state.toolDisplayMode);
                if (toolDisplayMode === "inline") {
                  return state.toolLinkBehavior === "inline"
                    ? "Current behavior: clicking a tool link expands the matching inline tool card in chat."
                    : "Current behavior: clicking a tool link focuses the matching item in the agent console while tool cards stay inline in chat.";
                }
                if (toolDisplayMode === "both") {
                  return state.toolLinkBehavior === "inline"
                    ? "Current behavior: clicking a tool link expands the matching inline tool card in chat, and the agent console still keeps the same tool activity available."
                    : "Current behavior: clicking a tool link focuses the matching item in the agent console while inline tool cards also stay visible in chat.";
                }
                if (toolDisplayMode === "auto") {
                  return state.toolLinkBehavior === "inline"
                    ? "Current behavior: clicking a tool link prefers the inline tool card on the active message, while the agent console continues to handle non-active tool activity."
                    : "Current behavior: clicking a tool link focuses the matching item in the agent console, while auto mode still shows inline cards for the active or streaming message.";
                }
                return "Current behavior: clicking a tool link opens the agent console because tool details are set to appear there.";
              })()}
            </p>
          </div>

          <div className="settings-section">
            <h3>Work history</h3>
            <label
              className="field-label"
              htmlFor="work-history-retention"
              title="How long reversible file, memory, calendar, and settings snapshots are kept."
            >
              How long reversible history is kept
            </label>
            <select
              id="work-history-retention"
              value={String(actionHistoryRetentionDays)}
              onChange={(event) => setActionHistoryRetentionDays(Number(event.target.value) || 0)}
            >
              {ACTION_HISTORY_RETENTION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="status-note" style={{ marginTop: 6 }}>
              Tracks reversible snapshots for file edits, memory changes, calendar writes, and
              similar state updates. Older copies are discarded after this window.
            </p>
            <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="icon-btn"
                onClick={handleActionHistorySave}
                disabled={actionHistorySaving}
                style={{ marginTop: 0 }}
              >
                {actionHistorySaving ? "Saving..." : "Save work history"}
              </button>
              <Link
                to="/work-history"
                className="icon-btn"
                style={{
                  marginTop: 0,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  textDecoration: "none",
                  color: "var(--color-black)",
                }}
              >
                Open work history
              </Link>
            </div>
            {actionHistoryMessage && <p className="status-note">{actionHistoryMessage}</p>}
          </div>

          <div className="settings-section">
            <div className="settings-header">
              <h3>Capture &amp; workflows</h3>
              <button
                type="button"
                className="icon-btn"
                onClick={refreshWorkflowCatalog}
                disabled={workflowCatalogLoading}
                style={{ marginTop: 0 }}
              >
                {workflowCatalogLoading ? "Refreshing..." : "Refresh profiles"}
              </button>
            </div>
            <label
              className="field-label"
              htmlFor="capture-retention"
              title="How long transient computer, screen, and camera captures stay available before pruning."
            >
              How long transient captures are kept
            </label>
            <select
              id="capture-retention"
              value={String(captureRetentionDays)}
              onChange={(event) => setCaptureRetentionDays(Number(event.target.value) || 7)}
            >
              {CAPTURE_RETENTION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <label
              className="field-label"
              htmlFor="capture-sensitivity"
              title="Default sensitivity label attached when a capture is created."
            >
              Default capture sensitivity
            </label>
            <select
              id="capture-sensitivity"
              value={captureDefaultSensitivity}
              onChange={(event) => setCaptureDefaultSensitivity(event.target.value)}
            >
              {CAPTURE_SENSITIVITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <div className="inline-flex" style={{ gap: 12, marginTop: 10, flexWrap: "wrap" }}>
              <label
                className="checkbox-row"
                style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
              >
                <input
                  type="checkbox"
                  checked={captureAllowModelRawImageAccess}
                  onChange={(event) =>
                    setCaptureAllowModelRawImageAccess(event.target.checked)
                  }
                />
                <span>Allow raw image access for supported models</span>
              </label>
              <label
                className="checkbox-row"
                style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
              >
                <input
                  type="checkbox"
                  checked={captureAllowSummaryFallback}
                  onChange={(event) => setCaptureAllowSummaryFallback(event.target.checked)}
                />
                <span>Allow summary fallback when raw images are restricted</span>
              </label>
            </div>
            <p className="status-note" style={{ marginTop: 6 }}>
              Computer observations, camera captures, and screen stills stay transient for this
              window unless promoted. Promoted captures remain accessible as durable attachments for
              later memory workflows.
            </p>
            <label
              className="field-label"
              htmlFor="default-workflow"
              title="Default workflow profile for new messages and auto-continues."
            >
              Default workflow profile
            </label>
            <select
              id="default-workflow"
              value={defaultWorkflow}
              onChange={(event) => setDefaultWorkflow(event.target.value)}
            >
              {(workflowCatalog.workflows.length
                ? workflowCatalog.workflows
                : DEFAULT_WORKFLOW_CATALOG.workflows
              ).map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.label}
                </option>
              ))}
            </select>
            <p className="status-note" style={{ marginTop: 6 }}>
              {(() => {
                const workflows = workflowCatalog.workflows.length
                  ? workflowCatalog.workflows
                  : DEFAULT_WORKFLOW_CATALOG.workflows;
                const selected =
                  workflows.find((workflow) => workflow.id === defaultWorkflow) || workflows[0];
                if (!selected) return "Workflow profiles control reasoning depth, recursion, and tool posture.";
                const preferredContinue = selected.preferred_continue || "the active workflow";
                return `${selected.description} Continue defaults prefer ${preferredContinue}.`;
              })()}
            </p>
            <div style={{ marginTop: 12 }}>
              <div className="field-label" style={{ marginBottom: 8 }}>
                Enabled modules
              </div>
              <div style={{ display: "grid", gap: 8 }}>
                {(workflowCatalog.modules.length
                  ? workflowCatalog.modules
                  : DEFAULT_WORKFLOW_CATALOG.modules
                ).map((module) => (
                  <label
                    key={module.id}
                    className="checkbox-row"
                    style={{
                      display: "grid",
                      gap: 4,
                      padding: "10px 12px",
                      border: "1px solid var(--glass-border)",
                      borderRadius: 12,
                    }}
                  >
                    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={enabledWorkflowModules.includes(module.id)}
                        onChange={(event) => {
                          setEnabledWorkflowModules((prev) => {
                            const current = Array.isArray(prev) ? prev : [];
                            if (event.target.checked) {
                              return Array.from(new Set([...current, module.id]));
                            }
                            return current.filter((item) => item !== module.id);
                          });
                        }}
                      />
                      <strong>{module.label}</strong>
                      <span className="status-note">({module.status || "live"})</span>
                    </span>
                    <span className="status-note" style={{ margin: 0 }}>
                      {module.description}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <p className="status-note" style={{ marginTop: 10 }}>
              Custom add-ons live in{" "}
              <code>{workflowCatalog.addons_root || DEFAULT_WORKFLOW_CATALOG.addons_root}</code>.
              {Array.isArray(workflowCatalog.addons) && workflowCatalog.addons.length > 0
                ? ` ${workflowCatalog.addons.length} add-on${
                    workflowCatalog.addons.length === 1 ? "" : "s"
                  } currently registered.`
                : " Drop sanctioned workflow/module packs there to surface them here later."}
            </p>
            <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="icon-btn"
                onClick={handleCaptureWorkflowSave}
                disabled={captureWorkflowSaving}
                style={{ marginTop: 0 }}
              >
                {captureWorkflowSaving ? "Saving..." : "Save capture defaults"}
              </button>
            </div>
            {captureWorkflowMessage && <p className="status-note">{captureWorkflowMessage}</p>}
          </div>

          <div className="settings-section">
            <div className="settings-header">
              <h3>Tools</h3>
              <button
                type="button"
                className="icon-btn"
                onClick={refreshToolCatalog}
                disabled={toolCatalogLoading}
                style={{ marginTop: 0 }}
              >
                {toolCatalogLoading ? "Refreshing..." : "Refresh tools"}
              </button>
            </div>
            <p className="status-note">
              Read-only browser for built-ins today, with source status for MCP and future custom
              tool management.
            </p>
            <div className="tool-browser-source-card" style={{ marginBottom: 12 }}>
              <div className="status-header">
                <strong>Computer use</strong>
                {renderToolStatusBadge(
                  filteredToolCatalog.some((entry) => String(entry?.id || "").startsWith("computer."))
                    ? "live"
                    : "experimental",
                )}
              </div>
              <p>
                Browser computer-use is exposed through the shared tool catalog. Windows desktop
                control is available as an experimental runtime and may require extra host
                dependencies.
              </p>
              <div className="tool-browser-source-meta">
                <span>
                  Browser tools:{" "}
                  {
                    filteredToolCatalog.filter((entry) =>
                      ["computer.observe", "computer.act", "computer.navigate", "open_url"].includes(
                        String(entry?.id || ""),
                      ),
                    ).length
                  }
                </span>
                <span>
                  Windows tools:{" "}
                  {
                    filteredToolCatalog.filter((entry) =>
                      String(entry?.id || "").startsWith("computer.windows.") ||
                      String(entry?.id || "") === "computer.app.launch",
                    ).length
                  }
                </span>
                <span>Shell + patch share the same approval flow.</span>
              </div>
            </div>
            <div className="tool-browser-source-grid">
              {toolSourceCards.map((card) => (
                <article key={card.id} className="tool-browser-source-card">
                  <div className="status-header">
                    <strong>{card.label}</strong>
                    {card.id === "mcp"
                      ? renderStatusBadge(card.badge)
                      : renderToolStatusBadge(card.badge)}
                  </div>
                  <p>{card.description}</p>
                  <div className="tool-browser-source-meta">
                    {card.details.map((detail) => (
                      <span key={`${card.id}-${detail}`}>{detail}</span>
                    ))}
                  </div>
                </article>
              ))}
            </div>
            {toolLimits && (
              <div className="tool-browser-limits">
                <div className="tool-browser-limit-row">
                  <span>Data root</span>
                  <code>{toolLimits?.roots?.data || "-"}</code>
                </div>
                <div className="tool-browser-limit-row">
                  <span>Workspace root</span>
                  <code>{toolLimits?.roots?.workspace || "-"}</code>
                </div>
                <div className="tool-browser-limit-row">
                  <span>Common caps</span>
                  <code>
                    {`search ${toolLimits?.limits?.search_web_max_results ?? "-"} • crawl ${toolLimits?.limits?.crawl_response_chars ?? "-"} chars • list_dir ${toolLimits?.limits?.list_dir_max_entries ?? "-"}`}
                  </code>
                </div>
              </div>
            )}
            <label htmlFor="tool-catalog-filter" title="Filter tools by name, category, or description.">
              Filter tools
            </label>
            <input
              id="tool-catalog-filter"
              type="text"
              value={toolCatalogFilter}
              onChange={(e) => setToolCatalogFilter(e.target.value)}
              placeholder="search_web, files, stub..."
            />
            <p className="status-note" style={{ marginTop: 6 }}>
              {toolCatalogLoading
                ? "Loading tool metadata..."
                : `${filteredToolCatalog.length} shown • ${toolStatusSummary.live} live • ${toolStatusSummary.stub} stub • ${toolStatusSummary.legacy} legacy`}
            </p>
            {toolCatalogError && (
              <p className="status-note warn" style={{ marginTop: 6 }}>
                {toolCatalogError}
              </p>
            )}
            {!toolCatalogLoading && !toolCatalogError && filteredToolCatalog.length === 0 ? (
              <div className="tool-browser-empty-state" role="status">
                <strong>No tools match &quot;{toolCatalogFilter.trim()}&quot;.</strong>
                <p>Try `live`, `memory`, `files`, or clear the filter.</p>
              </div>
            ) : (
              <div className="tool-browser-list">
                {filteredToolCatalog.map((entry) => {
                  const runtimeHints = [];
                  if (entry?.runtime?.executor) {
                    runtimeHints.push(`executor: ${entry.runtime.executor}`);
                  }
                  if (entry?.runtime?.network) runtimeHints.push("network");
                  if (entry?.runtime?.filesystem) runtimeHints.push("filesystem");
                  return (
                    <article key={entry.id} className="tool-browser-card">
                      <div className="status-header">
                        <div>
                          <div className="tool-browser-title-row">
                            <strong>{entry.display_name || entry.id}</strong>
                            <span className="tool-browser-code">{entry.id}</span>
                          </div>
                          <div className="status-sub">
                            <span>{entry.category || "tool"}</span>
                            <span>•</span>
                            <span>{entry.origin || "builtin"}</span>
                          </div>
                        </div>
                        {renderToolStatusBadge(entry.status)}
                      </div>
                      <p className="tool-browser-summary">
                        {entry.summary || entry.description || "No summary available."}
                      </p>
                      {runtimeHints.length > 0 && (
                        <div className="tool-browser-chip-row">
                          {runtimeHints.map((hint) => (
                            <span key={hint} className="tool-browser-chip">
                              {hint}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="tool-browser-detail-grid">
                        <div>
                          <span className="tool-browser-label">Can access</span>
                          <p>
                            {Array.isArray(entry.can_access) && entry.can_access.length
                              ? entry.can_access.slice(0, 2).join("; ")
                              : "No extra access notes."}
                          </p>
                        </div>
                        <div>
                          <span className="tool-browser-label">Limits</span>
                          <p>
                            {Array.isArray(entry.limit_hints) && entry.limit_hints.length
                              ? entry.limit_hints.slice(0, 2).join(" ")
                              : "No extra limits listed."}
                          </p>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>

            </section>
          )}

          {showSettingsSection("sharing") && (
            <section
              id="settings-sharing"
              className="settings-card settings-section"
              aria-label="Sharing and sync"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Sharing &amp; Sync</h2>
                  <p className="settings-card-copy">
                    Trusted-device sync and private live transport. This is the
                    visible entry point for the preview flow, not a public
                    account layer.
                  </p>
                </div>
              </div>

              <div className="settings-section">
                <p className="status-note">
                  Recommended use is a private LAN, VPN, or user-operated
                  tunnel. The current sync preview still expects a trusted
                  remote Float API and does not replace a real pairing wizard.
                </p>
                <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => document.getElementById("settings-output")?.scrollIntoView({ behavior: "smooth", block: "start" })}
                    style={{ marginTop: 0 }}
                  >
                    Open sync preview
                  </button>
                </div>
              </div>
            </section>
          )}

          {showSettingsSection("output") && (
            <section
              id="settings-output"
              className="settings-card settings-section"
              aria-label="Output and prompting"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Output &amp; Prompting</h2>
                  <p className="settings-card-copy">
                    Export defaults and reusable prompt instructions.
                  </p>
                </div>
              </div>

          <div className="settings-section">
            <h3>Conversation export</h3>
            <label title="Default format used when exporting conversations.">
              Default format
            </label>
            <select
              value={exportDefaults.format}
              onChange={(e) =>
                setExportDefaults((prev) => ({
                  ...prev,
                  format: normalizeExportFormat(e.target.value),
                }))
              }
            >
              <option value="md">Markdown</option>
              <option value="json">JSON</option>
              <option value="text">Text</option>
            </select>
            <label className="field-label" title="Default export channels.">
              <span>Default channels</span>
            </label>
            <div className="inline-flex" style={{ gap: 16, marginTop: 6, flexWrap: "wrap" }}>
              <label className="inline-flex" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={exportDefaults.includeChat}
                  onChange={(e) =>
                    setExportDefaults((prev) => ({
                      ...prev,
                      includeChat: e.target.checked,
                    }))
                  }
                />
                Chat
              </label>
              <label className="inline-flex" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={exportDefaults.includeThoughts}
                  onChange={(e) =>
                    setExportDefaults((prev) => ({
                      ...prev,
                      includeThoughts: e.target.checked,
                    }))
                  }
                />
                Thoughts
              </label>
              <label className="inline-flex" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={exportDefaults.includeTools}
                  onChange={(e) =>
                    setExportDefaults((prev) => ({
                      ...prev,
                      includeTools: e.target.checked,
                    }))
                  }
                />
                Tools
              </label>
            </div>
            <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="icon-btn"
                onClick={handleExportDefaultsSave}
                disabled={exportSaving}
                style={{ marginTop: 0 }}
              >
                {exportSaving ? "Saving..." : "Save defaults"}
              </button>
              <button
                type="button"
                className="icon-btn"
                onClick={handleExportAll}
                disabled={exportAllBusy}
                style={{ marginTop: 0 }}
              >
                {exportAllBusy ? "Exporting..." : "Export all"}
              </button>
            </div>
            {exportMessage && <p className="status-note">{exportMessage}</p>}
          </div>

          <div className="settings-section">
            <h3>Instance sync</h3>
            <p className="status-note">
              Preview a section-by-section merge against another Float instance,
              then pull its state here or push this instance there.
            </p>
            <label className="field-label" htmlFor="sync-remote-url">
              <span>Remote Float URL</span>
            </label>
            <input
              id="sync-remote-url"
              type="text"
              value={syncRemoteUrl}
              onChange={(event) => setSyncRemoteUrl(event.target.value)}
              placeholder="http://192.168.1.25:5000"
            />
            <label
              className="inline-flex"
              style={{ gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}
            >
              <input
                type="checkbox"
                checked={syncLinkToSourceDevice}
                onChange={(event) => setSyncLinkToSourceDevice(event.target.checked)}
              />
              Link synced data to its source device/workspace
            </label>
            <label className="field-label" htmlFor="sync-source-namespace">
              <span>This device label / namespace</span>
            </label>
            <input
              id="sync-source-namespace"
              type="text"
              value={syncSourceNamespace}
              onChange={(event) => setSyncSourceNamespace(event.target.value)}
              placeholder="desktop"
            />
            <p className="status-note">
              The remote instance must already be reachable over a private
              transport such as a LAN, VPN, or user-operated tunnel. This
              preview flow registers a temporary sync device automatically, but
              it is not a public discovery or login layer.
            </p>
            <p className="status-note">
              When source-linking is enabled, receivers keep synced
              conversations, attachments, memories, graph state, calendar
              events, and knowledge rows under a source namespace so nested
              device or workspace deployments can coexist.
            </p>
            <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="icon-btn"
                onClick={handleSyncDefaultsSave}
                disabled={syncDefaultsSaving}
                style={{ marginTop: 0 }}
              >
                {syncDefaultsSaving ? "Saving..." : "Save sync defaults"}
              </button>
              <button
                type="button"
                className="icon-btn"
                onClick={previewSync}
                disabled={syncBusy || syncActionBusy}
                style={{ marginTop: 0 }}
              >
                {syncBusy ? "Checking..." : "Preview sync"}
              </button>
            </div>
            {syncMessage && <p className="status-note">{syncMessage}</p>}
          </div>

          <div className="settings-section">
            <h3>System prompt</h3>
            <label
              className="field-label"
              title="Loaded from backend defaults; this section is not editable."
            >
              Default instructions (read-only)
            </label>
            <textarea
              className="message-field"
              rows="8"
              value={systemPromptBase}
              readOnly
            />
            <label
              className="field-label"
              title="Extra instructions appended after the default system prompt."
            >
              Custom instructions
            </label>
            <textarea
              className="message-field"
              rows="8"
              value={systemPromptCustom}
              onChange={(e) => setSystemPromptCustom(e.target.value)}
              placeholder="Add your custom behavior overrides here."
            />
            <div className="inline-flex" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="icon-btn"
                onClick={handleSystemPromptSave}
                disabled={systemPromptSaving}
                style={{ marginTop: 0 }}
              >
                {systemPromptSaving ? "Saving..." : "Save custom instructions"}
              </button>
            </div>
            {systemPromptMessage && <p className="status-note">{systemPromptMessage}</p>}
          </div>

            </section>
          )}

          {syncDialogOpen && syncPreview && (
            <div
              className="settings-sync-overlay"
              role="presentation"
              onClick={(event) => {
                if (event.target === event.currentTarget && !syncActionBusy) {
                  setSyncDialogOpen(false);
                }
              }}
            >
              <div
                className="settings-sync-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="sync-dialog-title"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="settings-sync-header">
                  <div>
                    <h3 id="sync-dialog-title">Sync preview</h3>
                    <p className="status-note">
                      Compare this Float instance with{" "}
                      {syncPreview?.remote?.base_url || syncRemoteUrl.trim()} and
                      choose which sections to merge.
                    </p>
                    {syncPreview?.link_to_source && (
                      <>
                        <p className="status-note">
                          Pull here will link remote data under{" "}
                          <code>{syncPullNamespace || "remote"}/</code>.
                        </p>
                        <p className="status-note">
                          Push there will link this instance under{" "}
                          <code>{syncPushNamespace || "this-device"}/</code> on the receiver.
                        </p>
                      </>
                    )}
                  </div>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => setSyncDialogOpen(false)}
                    disabled={!!syncActionBusy}
                    style={{ marginTop: 0 }}
                  >
                    Close
                  </button>
                </div>
                <div className="settings-sync-grid">
                  {syncPullSections.map((section) => {
                    const pushSection = syncPushSectionMap[section.key] || section;
                    return (
                    <label key={section.key} className="settings-sync-row">
                      <input
                        type="checkbox"
                        checked={!!syncSelections[section.key]}
                        onChange={(event) =>
                          setSyncSelections((prev) => ({
                            ...prev,
                            [section.key]: event.target.checked,
                          }))
                        }
                      />
                      <div>
                        <strong>{section.label}</strong>
                        <div className="status-note">
                          Pull here: Remote newer: {section.remote_newer} |
                          Local newer: {section.local_newer}
                        </div>
                        <div className="status-note">
                          Only remote: {section.only_remote} | Only local:{" "}
                          {section.only_local} | Identical: {section.identical}
                        </div>
                        <div className="status-note">
                          Push there: Remote newer: {pushSection.remote_newer}
                          {" | "}Local newer: {pushSection.local_newer}
                        </div>
                        <div className="status-note">
                          Only remote: {pushSection.only_remote} | Only local:{" "}
                          {pushSection.only_local} | Identical: {pushSection.identical}
                        </div>
                        {renderSyncPreviewItems("Pull item preview", section.items)}
                        {renderSyncPreviewItems("Push item preview", pushSection.items)}
                      </div>
                    </label>
                    );
                  })}
                </div>
                <div className="inline-flex" style={{ gap: 10, marginTop: 16, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => applySync("pull")}
                    disabled={!!syncActionBusy}
                    style={{ marginTop: 0 }}
                  >
                    {syncActionBusy === "pull" ? "Pulling..." : "Pull here"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => applySync("push")}
                    disabled={!!syncActionBusy}
                    style={{ marginTop: 0 }}
                  >
                    {syncActionBusy === "push" ? "Pushing..." : "Push there"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {message && <p className="settings-message">{message}</p>}

        </>

      )}

      {/* Global DownloadTray handles UI overlay; keep Settings minimal here. */}

    </div>

  );

};



export default Settings;

