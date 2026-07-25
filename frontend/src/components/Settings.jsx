import React, {
  useState,
  useEffect,
  useContext,
  useMemo,
  useRef,
  useCallback,
} from "react";
import { Link, useLocation } from "react-router-dom";

import { Line, Rect } from "./Skeleton";

import axios from "axios";

import "../styles/Settings.css";

import "../styles/ProgressBar.css";

import { GlobalContext } from "../main";
import StateInspector from "./StateInspector";
import {
  DEFAULT_VISUAL_THEME,
  getVisualTheme,
  getVisualThemeOptions,
  isBuiltInVisualTheme,
  normalizeVisualTheme,
  THEME_SLOT_KEYS,
} from "../theme";
import ModelJobsPanel from "./ModelJobsPanel";
import { normalizeToolDisplayMode } from "../utils/toolDisplayModes";

import { registerPush, unregisterPush } from "../utils/push";
import { filterAvailableModelsForField } from "../utils/modelFiltering";
import {
  buildModelGroups,
  DEFAULT_API_MODELS,
  formatApiModelLabel,
  formatLocalRuntimeLabel,
  isDirectLocalGemmaModel,
  isGemmaFamilyModel,
  isKnownDownloadableModel,
  isLocalRuntimeEntry,
  isKnownDirectDownloadModel,
  isProviderFirstGemmaModel,
  LOCAL_RUNTIME_ENTRIES,
  normalizeModelId,
  resolveLocalCatalogModelId,
  SUGGESTED_LOCAL_MODELS,
  SUGGESTED_SERVER_MODELS,
  isGptOssModel,
} from "../utils/modelUtils";
import {
  filterChatCapableProviderModels,
  formatProviderLastOperation,
  isChatCapableProviderModelName,
} from "../utils/providerRuntime";
import {
  RUNTIME_PANEL_LANES,
  resolveRuntimePanelContract,
} from "../utils/runtimePanelContract";
import {
  buildModelDeleteLockInspectorRows,
  buildProviderRuntimeInspectorRows,
  extractStateExplanationMessage,
  getStateExplanationSummary,
  getStateExplanationTitle,
} from "../utils/stateExplanations";
import {
  CAPTURE_SENSITIVITY_OPTIONS,
  getSensitivityTooltip,
} from "../utils/privacyLevels";
import {
  makeCustomServerPreset,
  normalizeServerPresets,
  selectedServerPreset,
  serverTrustWarning,
} from "../utils/serverPresets";

const FLOAT_SETTING_FIELDS = new Set([
  "gpu_memory_fraction",
  "gpu_memory_limit_gb",
  "cpu_offload_fraction",
  "cpu_offload_limit_gb",
  "request_timeout",
  "stream_idle_timeout",
  "rag_chat_min_similarity",
  "sae_threads_signal_blend",
  "background_autonomy_satisfied_threshold",
  "background_autonomy_min_priority",
]);

const INT_SETTING_FIELDS = new Set([
  "context_length",
  "gpu_memory_margin_mb",
  "cpu_thread_count",
  "local_provider_port",
  "local_provider_default_context_length",
  "background_autonomy_interval_seconds",
  "background_autonomy_max_reflections_per_tick",
  "background_autonomy_max_runtime_seconds",
  "background_autonomy_basic_tick_count",
  "background_autonomy_basic_tick_seconds",
]);

const normalizeHarmonyFormatMode = (value, fallback = "auto") => {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "auto" || raw === "enabled" || raw === "disabled") return raw;
  if (raw === "true" || raw === "1" || raw === "yes" || raw === "on") {
    return "enabled";
  }
  if (raw === "false" || raw === "0" || raw === "no" || raw === "off") {
    return "disabled";
  }
  return fallback === "enabled" || fallback === "disabled" ? fallback : "auto";
};

const resolveHarmonyFormat = (mode, prefersHarmony) => {
  const normalized = normalizeHarmonyFormatMode(mode);
  if (normalized === "disabled") return false;
  if (normalized === "enabled") return true;
  return !!prefersHarmony;
};

const MANAGED_LOCAL_PROVIDERS = new Set([
  "lmstudio",
  "ollama",
  "custom-openai-compatible",
]);

const PROVIDER_RUNTIME_STATUS_POLL_MS = 60000;

const LANGUAGE_RUNTIME_LANE_LABELS = Object.freeze({
  [RUNTIME_PANEL_LANES.CLOUD_API]: "Cloud API",
  [RUNTIME_PANEL_LANES.SERVER_LAN]: "Server/LAN",
  [RUNTIME_PANEL_LANES.LOCAL_PROVIDER]: "Local provider",
  [RUNTIME_PANEL_LANES.DIRECT_LOCAL]: "Direct local",
});

const expandSettingsSearchTerm = (value) => {
  const term = String(value || "").trim().toLowerCase();
  if (!term) return [];
  const variants = new Set([term]);
  if (term.length > 3 && term.endsWith("s")) {
    variants.add(term.slice(0, -1));
  }
  if (term.length > 3 && !term.endsWith("s")) {
    variants.add(`${term}s`);
  }
  return Array.from(variants);
};

const SETTINGS_SECTIONS = [
  {
    id: "connections",
    label: "Connections",
    description: "Provider access, runtime endpoints, and device sync.",
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
      "runtime",
      "server",
      "server lan",
      "server lan url",
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
      "sharing",
      "sync",
      "instance sync",
      "pairing",
      "trusted device",
      "private transport",
      "tailnet",
      "vpn",
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
    description: "Notifications, appearance, work history, and tool browsing.",
    searchText: [
      "workspace",
      "appearance",
      "theme",
      "spring",
      "ash",
      "cappucino",
      "sunset citrus",
      "midnight plum",
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
      "work history",
      "write history",
      "notifications",
      "theme",
      "appearance",
    ].join(" "),
  },
  {
    id: "workflows",
    label: "Visual data",
    description: "Camera and screen capture retention, image access, and privacy routing.",
    searchText: [
      "capture",
      "capture privacy",
      "workflow",
      "workflows",
      "skills workflows knowledge",
      "default workflow",
      "capture retention",
      "capture sensitivity",
      "privacy filter",
      "automatic privacy",
      "memory sensitivity",
      "camera capture",
    ].join(" "),
  },
  {
    id: "background",
    label: "Background",
    description: "Autonomy budgets, dry runs, and queued long-running checks.",
    searchText: [
      "background",
      "autonomy",
      "overnight",
      "always on",
      "extended",
      "satisfied",
      "runtime budget",
      "max patience",
      "container",
      "orchestration",
      "queued tests",
      "reflection",
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

const PRIVACY_FILTER_MODE_OPTIONS = [
  {
    value: "off",
    label: "Never use it",
    description: "Do not run the automatic text privacy classifier on writes.",
  },
  {
    value: "auto",
    label: "Auto on writes",
    description: "Classify text writes and escalate sensitivity only when the user did not choose one.",
  },
  {
    value: "always",
    label: "Always check",
    description: "Classify text writes and report suggestions, while keeping explicit user choices.",
  },
];

const PRIVACY_FILTER_MODEL_PRESETS = [
  { label: "openai/privacy-filter", value: "openai/privacy-filter" },
  { label: "privacy-filter (download alias)", value: "privacy-filter" },
];

const PRIVACY_ROUTE_MODE_OPTIONS = [
  {
    value: "off",
    label: "Do not ask",
    description: "Do not suggest model routing based on the text privacy classifier.",
  },
  {
    value: "ask",
    label: "Ask before non-local model",
    description:
      "When protected or secret text is detected before an API/server call, pause and suggest continuing locally.",
  },
];

const normalizePrivacyFilterMode = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  return PRIVACY_FILTER_MODE_OPTIONS.some((option) => option.value === raw) ? raw : "off";
};

const normalizePrivacyRouteMode = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  return PRIVACY_ROUTE_MODE_OPTIONS.some((option) => option.value === raw) ? raw : "off";
};

const TOOL_WORKFLOW_OPTIONS = [
  { value: "disabled", label: "Disabled" },
  { value: "text", label: "Text" },
  { value: "live", label: "Live" },
  { value: "both", label: "Both" },
];

const TOOL_APPROVAL_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "high", label: "High" },
];

const normalizeToolWorkflow = (value, fallback = "text") => {
  const raw = String(value || "").trim().toLowerCase();
  return TOOL_WORKFLOW_OPTIONS.some((option) => option.value === raw)
    ? raw
    : fallback;
};

const normalizeToolApproval = (value, fallback = "high") => {
  const raw = String(value || "").trim().toLowerCase();
  return TOOL_APPROVAL_OPTIONS.some((option) => option.value === raw)
    ? raw
    : fallback;
};

const BACKGROUND_AUTONOMY_MODE_OPTIONS = [
  {
    value: "manual",
    label: "Manual",
    description: "Only run dry-runs or explicit ticks.",
  },
  {
    value: "basic",
    label: "Basic test",
    description: "Two short queued checks by default.",
  },
  {
    value: "overnight",
    label: "Overnight review",
    description: "Use the runtime budget, 30 minutes by default.",
  },
  {
    value: "extended",
    label: "Extended",
    description: "Stop when the satisfaction threshold is met.",
  },
  {
    value: "always_on",
    label: "Always on",
    description: "Keep polling until manually disabled.",
  },
];

const normalizeBackgroundAutonomyMode = (value) => {
  const raw = String(value || "").trim().toLowerCase().replace(/-/g, "_");
  return BACKGROUND_AUTONOMY_MODE_OPTIONS.some((option) => option.value === raw)
    ? raw
    : "overnight";
};

const normalizeToolPolicy = (policy) => ({
  workflow: normalizeToolWorkflow(policy?.workflow, "text"),
  approval: normalizeToolApproval(policy?.approval, "high"),
  workflows:
    policy?.workflows && typeof policy.workflows === "object"
      ? {
          text: !!policy.workflows.text,
          live: !!policy.workflows.live,
        }
      : {
          text: ["text", "both"].includes(normalizeToolWorkflow(policy?.workflow, "text")),
          live: ["live", "both"].includes(normalizeToolWorkflow(policy?.workflow, "text")),
        },
  live_auto: !!policy?.live_auto,
  live_unavailable_reason: String(policy?.live_unavailable_reason || ""),
});

const toolPolicyControlId = (toolId, field) =>
  `tool-policy-${field}-${String(toolId || "tool").replace(/[^a-z0-9_-]+/gi, "-")}`;

const THEME_SLOT_LABELS = {
  c1Light: "C1 light",
  c1Med: "C1 medium",
  c1Dark: "C1 dark",
  c2Light: "C2 light",
  c2Med: "C2 medium",
  c2Dark: "C2 dark",
  veryLight: "Very light",
  veryDark: "Very dark",
};

const buildThemeDraftFromTheme = (themeSource, label = "Custom Theme") => {
  const theme =
    themeSource && typeof themeSource === "object"
      ? themeSource
      : getVisualTheme(themeSource);
  const slots = THEME_SLOT_KEYS.reduce((acc, key) => {
    acc[key] = theme?.slots?.[key] || "#000000";
    return acc;
  }, {});
  return { label, slots };
};

const SettingsInfoTip = ({ text, label = "More information" }) => (
  <span
    className="settings-info-tip"
    tabIndex={0}
    role="note"
    aria-label={`${label}: ${text}`}
  >
    <span aria-hidden="true">?</span>
    <span className="settings-info-tip-content" role="tooltip">
      {text}
    </span>
  </span>
);

const Settings = () => {

  const location = useLocation();
  const settingsContainerRef = useRef(null);
  const settingsToolbarRef = useRef(null);
  const lastServerModelSyncRef = useRef(undefined);

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

  const [backgroundAutonomyStatus, setBackgroundAutonomyStatus] = useState(null);

  const [backgroundAutonomyLoading, setBackgroundAutonomyLoading] = useState(false);

  const [backgroundAutonomyMessage, setBackgroundAutonomyMessage] = useState("");

  const [backgroundAutonomyTickBusy, setBackgroundAutonomyTickBusy] = useState(false);

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

  const [themeEditorMode, setThemeEditorMode] = useState("none");
  const [themeDraftId, setThemeDraftId] = useState(null);
  const [themeDraftLabel, setThemeDraftLabel] = useState("Custom Theme");
  const [themeDraftSlots, setThemeDraftSlots] = useState(() =>
    buildThemeDraftFromTheme(DEFAULT_VISUAL_THEME).slots,
  );
  const [themeSaveBusy, setThemeSaveBusy] = useState(false);
  const [themeDeleteBusy, setThemeDeleteBusy] = useState(false);
  const [themeMessage, setThemeMessage] = useState("");
  const [activeSettingsSection, setActiveSettingsSection] = useState(
    SETTINGS_SECTIONS[0]?.id || "connections",
  );
  const [pendingSettingsScroll, setPendingSettingsScroll] = useState(null);



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

      const raw = Number(value);
      const normalized =
        Number.isFinite(raw) && raw > 0 && raw < 1e12 ? raw * 1000 : value;
      const date = new Date(normalized);

      if (Number.isNaN(date.getTime())) return null;

      return date.toLocaleString();

    } catch (err) {

      return null;

    }

  };

  const formatClockTime = (value) => {

    if (value == null || value === "") return null;

    const raw = Number(value);
    const normalized =
      Number.isFinite(raw) && raw > 0 && raw < 1e12 ? raw * 1000 : value;
    const date = new Date(normalized);

    if (Number.isNaN(date.getTime())) return null;

    return date.toLocaleTimeString([], {

      hour: "2-digit",

      minute: "2-digit",

      second: "2-digit",

    });

  };



  const formatRelativeTime = (value, now = Date.now()) => {

    if (value == null || value === "") return null;

    const raw = Number(value);
    const normalized = Number.isFinite(raw)
      ? raw > 0 && raw < 1e12
        ? raw * 1000
        : raw
      : new Date(value).getTime();
    const diff = now - normalized;

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

  const customThemes = Array.isArray(state.customThemes) ? state.customThemes : [];
  const visualThemeOptions = useMemo(
    () => getVisualThemeOptions(customThemes),
    [customThemes],
  );
  const selectedThemeId = normalizeVisualTheme(state.visualTheme);
  const selectedCustomTheme = useMemo(
    () =>
      customThemes.find(
        (theme) => normalizeVisualTheme(theme?.id || "") === selectedThemeId,
      ) || null,
    [customThemes, selectedThemeId],
  );
  const isEditingCustomTheme = themeEditorMode === "edit" && !!themeDraftId;
  const showThemeEditor = themeEditorMode === "new" || themeEditorMode === "edit";

  const syncCustomThemes = (themes) => {
    setState((prev) => ({
      ...prev,
      customThemes: Array.isArray(themes) ? themes : [],
    }));
  };

  const openNewThemeEditor = () => {
    const draft = buildThemeDraftFromTheme(
      selectedThemeId || DEFAULT_VISUAL_THEME,
      "Custom Theme",
    );
    setThemeEditorMode("new");
    setThemeDraftId(null);
    setThemeDraftLabel(draft.label);
    setThemeDraftSlots(draft.slots);
    setThemeMessage("");
  };

  const loadThemeDraft = (theme) => {
    const draft = buildThemeDraftFromTheme(theme, theme?.label || "Custom Theme");
    setThemeEditorMode("edit");
    setThemeDraftId(theme?.id || null);
    setThemeDraftLabel(theme?.label || draft.label);
    setThemeDraftSlots(draft.slots);
  };

  const closeThemeEditor = () => {
    setThemeEditorMode("none");
    setThemeDraftId(null);
    setThemeDraftLabel("Custom Theme");
    setThemeDraftSlots(buildThemeDraftFromTheme(selectedThemeId || DEFAULT_VISUAL_THEME).slots);
  };

  const saveThemeDraft = async () => {
    setThemeSaveBusy(true);
    setThemeMessage("");
    try {
      const response = await axios.post("/api/themes", {
        id: isEditingCustomTheme ? themeDraftId : null,
        label: themeDraftLabel,
        slots: themeDraftSlots,
      });
      const savedTheme = response?.data?.theme;
      const nextThemes = [...customThemes.filter((theme) => theme.id !== savedTheme.id), savedTheme]
        .sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)));
      syncCustomThemes(nextThemes);
      setState((prev) => ({
        ...prev,
        visualTheme: normalizeVisualTheme(savedTheme.id),
      }));
      loadThemeDraft(savedTheme);
      setThemeMessage("Theme saved.");
    } catch (error) {
      setThemeMessage(
        error?.response?.data?.detail || "Failed to save theme.",
      );
    } finally {
      setThemeSaveBusy(false);
    }
  };

  const deleteThemeDraft = async () => {
    if (!themeDraftId) return;
    setThemeDeleteBusy(true);
    setThemeMessage("");
    try {
      await axios.delete(`/api/themes/${themeDraftId}`);
      const nextThemes = customThemes.filter((theme) => theme.id !== themeDraftId);
      syncCustomThemes(nextThemes);
      setState((prev) => ({
        ...prev,
        visualTheme:
          normalizeVisualTheme(prev.visualTheme) === normalizeVisualTheme(themeDraftId)
            ? DEFAULT_VISUAL_THEME
            : prev.visualTheme,
      }));
      closeThemeEditor();
      setThemeMessage("Theme deleted.");
    } catch (error) {
      setThemeMessage(
        error?.response?.data?.detail || "Failed to delete theme.",
      );
    } finally {
      setThemeDeleteBusy(false);
    }
  };

  useEffect(() => {
    axios
      .get("/api/themes")
      .then((response) => {
        const themes = Array.isArray(response?.data?.themes) ? response.data.themes : [];
        syncCustomThemes(themes);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedCustomTheme && themeEditorMode !== "new") {
      const selectedId = normalizeVisualTheme(selectedCustomTheme?.id || "");
      const draftId = normalizeVisualTheme(themeDraftId || "");
      if (!showThemeEditor || selectedId !== draftId) {
        loadThemeDraft(selectedCustomTheme);
      }
      return;
    }
    if (!selectedCustomTheme && themeEditorMode === "edit") {
      closeThemeEditor();
    }
  }, [selectedCustomTheme, themeEditorMode, themeDraftId, showThemeEditor]);

  const formatCeleryStatusNote = (value) => {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const lowered = raw.toLowerCase();
    if (lowered.includes("broker") || lowered.includes("redis")) {
      return `${raw}. Background jobs are paused until the broker and worker are running.`;
    }
    return raw;
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

      note = formatCeleryStatusNote(snapshot.error);

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

    setBackgroundAutonomyLoading(true);
    try {
      const autonomyRes = await axios.get("/api/background/autonomy/status");
      const autonomyData =
        autonomyRes && typeof autonomyRes.data === "object"
          ? autonomyRes.data.autonomy
          : null;
      setBackgroundAutonomyStatus(
        autonomyData && typeof autonomyData === "object" ? autonomyData : null,
      );
    } catch {
      setBackgroundAutonomyStatus({ error: "unreachable" });
    } finally {
      setBackgroundAutonomyLoading(false);
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
  harmony_format_mode: normalizeHarmonyFormatMode(state.harmonyFormatMode),

  server_url: state.serverUrl,
  server_preset_id: "",
  server_presets: [],

    stt_model: state.sttModel,

    tts_model: state.ttsModel,

    voice_model: state.voiceModel,
    stream_backend: "api",
    realtime_model: "gpt-realtime-2.1",
    realtime_voice: "alloy",
    live_agent_mode: "local",
    live_agent_model: "",
    live_multimodal_model: "",
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
    rag_chat_min_similarity: 0.45,
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
    background_autonomy_enabled: false,
    background_autonomy_sandbox_processes: true,
    background_autonomy_mode: "overnight",
    background_autonomy_interval_seconds: 900,
    background_autonomy_max_reflections_per_tick: 1,
    background_autonomy_max_runtime_seconds: 1800,
    background_autonomy_satisfied_threshold: 0.8,
    background_autonomy_basic_tick_count: 2,
    background_autonomy_basic_tick_seconds: 300,
    background_autonomy_min_priority: 0.05,
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
  const [privacyFilterMode, setPrivacyFilterMode] = useState("off");
  const [privacyFilterModel, setPrivacyFilterModel] = useState("openai/privacy-filter");
  const [privacyRouteMode, setPrivacyRouteMode] = useState("off");
  const [capturePrivacySaving, setCapturePrivacySaving] = useState(false);
  const [capturePrivacyMessage, setCapturePrivacyMessage] = useState("");

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
  const [settingsSearch, setSettingsSearch] = useState("");
  const [toolCatalog, setToolCatalog] = useState([]);
  const [toolLimits, setToolLimits] = useState(null);
  const [toolCatalogLoading, setToolCatalogLoading] = useState(false);
  const [toolCatalogError, setToolCatalogError] = useState("");
  const [toolCatalogFilter, setToolCatalogFilter] = useState("");
  const [toolPolicySaving, setToolPolicySaving] = useState("");
  const [toolPolicyMessage, setToolPolicyMessage] = useState("");
  const [hfRegisterOpen, setHfRegisterOpen] = useState(false);

  const [availableModels, setAvailableModels] = useState([]);
  const [providerRuntime, setProviderRuntime] = useState(null);
  const [providerRuntimeLoading, setProviderRuntimeLoading] = useState(false);
  const [providerRuntimeError, setProviderRuntimeError] = useState("");
  const [providerModelOptions, setProviderModelOptions] = useState([]);
  const [providerActionBusy, setProviderActionBusy] = useState("");
  const [providerActionMessage, setProviderActionMessage] = useState("");
  const [localRuntime, setLocalRuntime] = useState(null);
  const [localRuntimeLoading, setLocalRuntimeLoading] = useState(false);
  const [localRuntimeError, setLocalRuntimeError] = useState("");
  const [localRuntimeActionBusy, setLocalRuntimeActionBusy] = useState("");
  const [localRuntimeMessage, setLocalRuntimeMessage] = useState("");
  const [serverRuntime, setServerRuntime] = useState(null);
  const [serverRuntimeLoading, setServerRuntimeLoading] = useState(false);
  const [serverRuntimeError, setServerRuntimeError] = useState("");
  const [languageRuntimeCollapsed, setLanguageRuntimeCollapsed] = useState(false);
  const [embeddingRuntimeCollapsed, setEmbeddingRuntimeCollapsed] = useState(false);
  const [embeddingRuntimeBusy, setEmbeddingRuntimeBusy] = useState("");
  const [embeddingRuntimeMessage, setEmbeddingRuntimeMessage] = useState("");
  const [runtimeNow, setRuntimeNow] = useState(() => Date.now());
  const [includeCacheUnfiltered, setIncludeCacheUnfiltered] = useState(false);
  const [showDownloadedOnly, setShowDownloadedOnly] = useState(false);
  const [registeredLocalModels, setRegisteredLocalModels] = useState([]);
  const [registerModelAlias, setRegisterModelAlias] = useState("");
  const [registerModelPath, setRegisterModelPath] = useState("");
  const [registerModelType, setRegisterModelType] = useState("transformer");
  const [registerModelBusy, setRegisterModelBusy] = useState(false);
  const [registerModelMessage, setRegisterModelMessage] = useState("");
  const [registerModelExplanation, setRegisterModelExplanation] = useState(null);
  const [registerHfUrl, setRegisterHfUrl] = useState("");
  const [registerHfAlias, setRegisterHfAlias] = useState("");
  const [registerHfType, setRegisterHfType] = useState("transformer");
  const [registerHfRuntime, setRegisterHfRuntime] = useState("direct");
  const [registerHfBusy, setRegisterHfBusy] = useState(false);
  const [registerHfMessage, setRegisterHfMessage] = useState("");
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

  const providerModelOptionsSet = useMemo(() => {
    const set = new Set();
    (providerModelOptions || []).forEach((model) => {
      if (typeof model === "string" && model.trim()) {
        set.add(model.trim());
      }
    });
    return set;
  }, [providerModelOptions]);

  const providerPreferredModelOptions = useMemo(() => {
    const seen = new Set();
    const options = [];
    const add = (value) => {
      const model = typeof value === "string" ? value.trim() : "";
      if (!model || seen.has(model)) return;
      seen.add(model);
      options.push(model);
    };
    (providerModelOptions || []).forEach(add);
    add(settings.local_provider_preferred_model);
    return options;
  }, [providerModelOptions, settings.local_provider_preferred_model]);

  const apiModelsAvailable = Array.isArray(state.apiModels) ? state.apiModels : [];
  const apiModelAliases =
    state.apiModelAliases && typeof state.apiModelAliases === "object"
      ? state.apiModelAliases
      : {};
  const apiModelCatalog = Array.isArray(state.apiModelCatalog)
    ? state.apiModelCatalog
    : [];
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
        description: "Saved HTTP or MCP tools will appear here when custom creation is available.",
        details: ["None configured", "Creation is not available yet"],
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
  useEffect(() => {
    const timerId = window.setInterval(() => {
      setRuntimeNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timerId);
  }, []);
  const settingsSearchTerms = useMemo(
    () =>
      String(settingsSearch || "")
        .trim()
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean)
        .map(expandSettingsSearchTerm),
    [settingsSearch],
  );
  const visibleSettingsSections = useMemo(() => {
    if (!settingsSearchTerms.length) return SETTINGS_SECTIONS;
    return SETTINGS_SECTIONS.filter((section) =>
      settingsSearchTerms.every((terms) =>
        terms.some((term) => section.searchText.includes(term)),
      ),
    );
  }, [settingsSearchTerms]);
  const visibleSettingsSectionIds = useMemo(
    () => new Set(visibleSettingsSections.map((section) => section.id)),
    [visibleSettingsSections],
  );
  const visibleSettingsSectionList = useMemo(
    () => visibleSettingsSections.map((section) => section.id),
    [visibleSettingsSections],
  );
  const showSettingsSection = (sectionId) =>
    !settingsSearchTerms.length || visibleSettingsSectionIds.has(sectionId);

  useEffect(() => {
    if (!visibleSettingsSectionList.length) return;
    if (!visibleSettingsSectionList.includes(activeSettingsSection)) {
      setActiveSettingsSection(visibleSettingsSectionList[0]);
    }
  }, [activeSettingsSection, visibleSettingsSectionList]);

  const getSettingsToolbarOffset = useCallback(() => {
    const toolbarHeight = settingsToolbarRef.current?.offsetHeight || 0;
    return toolbarHeight + 18;
  }, []);

  const scrollToSettingsSection = useCallback(
    (sectionId, { behavior = "smooth" } = {}) => {
      const container = settingsContainerRef.current;
      const target = document.getElementById(`settings-${sectionId}`);
      if (!container || !target) return false;
      setActiveSettingsSection(sectionId);
      const nextTop = Math.max(0, target.offsetTop - getSettingsToolbarOffset());
      if (typeof container.scrollTo === "function") {
        container.scrollTo({
          top: nextTop,
          behavior,
        });
      } else {
        container.scrollTop = nextTop;
      }
      return true;
    },
    [getSettingsToolbarOffset],
  );
  const hashSettingsSection = useMemo(() => {
    const rawHash = String(location.hash || "").replace(/^#/, "");
    const sectionId = rawHash.startsWith("settings-")
      ? rawHash.slice("settings-".length)
      : rawHash;
    if (!sectionId) return "";
    return SETTINGS_SECTIONS.some((section) => section.id === sectionId)
      ? sectionId
      : "";
  }, [location.hash]);

  useEffect(() => {
    if (!hashSettingsSection) return;
    if (settingsSearchTerms.length > 0) {
      setSettingsSearch("");
    }
    setPendingSettingsScroll(hashSettingsSection);
  }, [hashSettingsSection, settingsSearchTerms.length]);

  useEffect(() => {
    const container = settingsContainerRef.current;
    if (!container || !visibleSettingsSectionList.length || loading) return undefined;

    const resolveActiveSection = () => {
      const scrollAnchor = container.scrollTop + getSettingsToolbarOffset();
      let nextActive = visibleSettingsSectionList[0];
      visibleSettingsSectionList.forEach((sectionId) => {
        const section = document.getElementById(`settings-${sectionId}`);
        if (!section) return;
        if (section.offsetTop <= scrollAnchor) {
          nextActive = sectionId;
        }
      });
      setActiveSettingsSection((prev) => (prev === nextActive ? prev : nextActive));
    };

    let frameId = null;
    const queueResolve = () => {
      if (frameId !== null || typeof window === "undefined") return;
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        resolveActiveSection();
      });
    };

    queueResolve();
    container.addEventListener("scroll", queueResolve, { passive: true });
    window.addEventListener("resize", queueResolve);
    return () => {
      if (frameId !== null && typeof window !== "undefined") {
        window.cancelAnimationFrame(frameId);
      }
      container.removeEventListener("scroll", queueResolve);
      window.removeEventListener("resize", queueResolve);
    };
  }, [getSettingsToolbarOffset, loading, visibleSettingsSectionList]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const ignoredScrollTransferTarget = (target) =>
      !target ||
      target.closest(
        ".sidebar, .history-sidebar, .agent-console, .topbar-appbar, [role='dialog'], .modal, .download-tray",
      );

    const transferWheelToSettings = (event) => {
      const container = settingsContainerRef.current;
      if (!container || event.defaultPrevented) return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || container.contains(target)) return;
      if (ignoredScrollTransferTarget(target)) {
        return;
      }
      const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
      if (maxScrollTop <= 0) return;
      const nextScrollTop = Math.max(
        0,
        Math.min(maxScrollTop, container.scrollTop + event.deltaY),
      );
      if (nextScrollTop === container.scrollTop && !event.deltaX) return;
      event.preventDefault();
      container.scrollTop = nextScrollTop;
      if (event.deltaX) {
        container.scrollLeft += event.deltaX;
      }
    };

    window.addEventListener("wheel", transferWheelToSettings, { passive: false });
    return () => {
      window.removeEventListener("wheel", transferWheelToSettings);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    let dragState = null;
    const ignoredDragTransferTarget = (target) =>
      !target ||
      target.closest(
        ".sidebar, .history-sidebar, .agent-console, .topbar-appbar, [role='dialog'], .modal, .download-tray",
      ) ||
      target.closest(
        "a, button, input, select, textarea, summary, label, [role='button'], [role='tab'], [contenteditable='true']",
      );

    const endDragScroll = () => {
      dragState = null;
      document.body.classList.remove("settings-drag-scroll-active");
    };

    const handlePointerMove = (event) => {
      if (!dragState) return;
      const container = settingsContainerRef.current;
      if (!container) {
        endDragScroll();
        return;
      }
      const dx = event.clientX - dragState.lastX;
      const dy = event.clientY - dragState.lastY;
      if (dx || dy) {
        container.scrollLeft -= dx;
        container.scrollTop -= dy;
        dragState.lastX = event.clientX;
        dragState.lastY = event.clientY;
        event.preventDefault();
      }
    };

    const handlePointerUp = () => {
      endDragScroll();
    };

    const handlePointerDown = (event) => {
      if (event.button !== 0 || event.defaultPrevented) return;
      const container = settingsContainerRef.current;
      if (!container) return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || container.contains(target) || ignoredDragTransferTarget(target)) {
        return;
      }
      const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
      const maxScrollLeft = Math.max(0, container.scrollWidth - container.clientWidth);
      if (maxScrollTop <= 0 && maxScrollLeft <= 0) return;
      dragState = {
        lastX: event.clientX,
        lastY: event.clientY,
      };
      document.body.classList.add("settings-drag-scroll-active");
      event.preventDefault();
    };

    window.addEventListener("pointerdown", handlePointerDown, { passive: false });
    window.addEventListener("pointermove", handlePointerMove, { passive: false });
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
      endDragScroll();
    };
  }, []);

  useEffect(() => {
    if (!pendingSettingsScroll || loading || typeof window === "undefined") {
      return undefined;
    }
    if (
      settingsSearchTerms.length > 0 &&
      !visibleSettingsSectionIds.has(pendingSettingsScroll)
    ) {
      return undefined;
    }
    const frameId = window.requestAnimationFrame(() => {
      if (scrollToSettingsSection(pendingSettingsScroll)) {
        setPendingSettingsScroll((current) =>
          current === pendingSettingsScroll ? null : current,
        );
      }
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [
    loading,
    pendingSettingsScroll,
    scrollToSettingsSection,
    settingsSearchTerms.length,
    visibleSettingsSectionIds,
  ]);

  const handleSettingsNavClick = useCallback(
    (sectionId) => {
      if (settingsSearchTerms.length > 0 && !visibleSettingsSectionIds.has(sectionId)) {
        setPendingSettingsScroll(sectionId);
        setSettingsSearch("");
        return;
      }
      setPendingSettingsScroll(null);
      scrollToSettingsSection(sectionId);
    },
    [scrollToSettingsSection, settingsSearchTerms.length, visibleSettingsSectionIds],
  );
  const suggestedLangModels = Array.from(
    new Set([
      ...(Array.isArray(SUGGESTED_LOCAL_MODELS) ? SUGGESTED_LOCAL_MODELS : []),
      ...(Array.isArray(LOCAL_RUNTIME_ENTRIES) ? LOCAL_RUNTIME_ENTRIES : []),
    ]),
  );
  const serverRuntimeModelOptions = Array.from(
    new Set([
      ...(Array.isArray(serverRuntime?.models) ? serverRuntime.models : []),
      ...(Array.isArray(providerModelOptions) ? providerModelOptions : []),
      ...(Array.isArray(SUGGESTED_SERVER_MODELS) ? SUGGESTED_SERVER_MODELS : []),
      settings.transformer_model,
    ].filter((model) => typeof model === "string" && model.trim())),
  );
  const activeServerPreset = selectedServerPreset(settings);
  const activeServerTrustWarning = serverTrustWarning(settings);
  const suggestedApiLangModels = apiModelGroups.all;

  const suggestedSttModels = [
    "gpt-realtime-whisper",
    "whisper-1",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "whisper-large-v3-turbo",
    "whisper-small",
  ];

  const suggestedTtsModels = [
    "tts-1",
    "tts-1-hd",
    "gpt-4o-mini-tts",
    "gpt-4o-mini-tts-2025-12-15",
    "kokoro",
    "kitten",
  ];

  const languageToolingFamilies = new Set([
    "gpt-oss",
    "gemma",
    "llama",
    "qwen",
    "mistral",
    "mixtral",
  ]);

  const inferModelFamily = (value, info = null) => {
    const normalized = String(value || "").trim().toLowerCase();
    const metadataFamily = String(info?.metadata?.family || "")
      .trim()
      .toLowerCase();
    if (metadataFamily) return metadataFamily;
    if (!normalized) return "";
    if (normalized.startsWith("gpt-oss")) return "gpt-oss";
    if (normalized.startsWith("gemma-")) return "gemma";
    if (normalized.startsWith("llama")) return "llama";
    if (normalized.startsWith("qwen")) return "qwen";
    if (normalized.startsWith("mistral")) return "mistral";
    if (normalized.startsWith("mixtral")) return "mixtral";
    if (normalized.includes("paligemma")) return "paligemma";
    if (normalized.includes("pixtral")) return "pixtral";
    if (normalized.includes("clip")) return "clip";
    if (normalized.includes("whisper")) return "whisper";
    if (normalized.includes("kokoro")) return "kokoro";
    if (normalized.includes("kitten")) return "kitten";
    return "";
  };

  const getModelLaneMeta = (field, value, info = null) => {
    const raw = String(value || "").trim();
    const normalizedField = String(field || "").trim().toLowerCase();
    const normalizedValue = raw.toLowerCase();
    if (!raw) return null;
    if (normalizedField === "transformer_model") {
      if (isLocalRuntimeEntry(raw)) {
        return { key: "provider", label: "Server / LAN" };
      }
      if (
        isDirectLocalGemmaModel(raw) ||
        info?.lane === "local" ||
        info?.local_download_supported ||
        isKnownDirectDownloadModel(raw)
      ) {
        return { key: "local", label: "Local" };
      }
      if (
        isProviderFirstGemmaModel(raw) ||
        info?.lane === "server_lan" ||
        (info?.provider_supported && !info?.local_download_supported)
      ) {
        return { key: "provider", label: "Server / LAN" };
      }
      return { key: "local", label: "Local" };
    }
    if (normalizedField === "stt_model") {
      if (
        normalizedValue === "whisper-1" ||
        normalizedValue === "gpt-realtime-whisper" ||
        normalizedValue.startsWith("gpt-4o") ||
        normalizedValue.includes("transcribe")
      ) {
        return { key: "api", label: "API" };
      }
      return { key: "local", label: "Local" };
    }
    if (normalizedField === "tts_model") {
      if (
        normalizedValue.startsWith("tts-") ||
        (normalizedValue.startsWith("gpt-4o") && normalizedValue.includes("tts"))
      ) {
        return { key: "api", label: "API" };
      }
      return { key: "local", label: "Local" };
    }
      if (normalizedField === "vision_model") {
        return { key: "local", label: "Local" };
      }
      if (
        normalizedField === "live_agent_model" ||
        normalizedField === "live_multimodal_model"
      ) {
        if (isLocalRuntimeEntry(raw)) {
          return { key: "provider", label: "Server / LAN" };
        }
        if (
          isDirectLocalGemmaModel(raw) ||
          info?.lane === "local" ||
          info?.local_download_supported ||
          isKnownDirectDownloadModel(raw)
        ) {
          return { key: "local", label: "Local" };
        }
        if (
          isProviderFirstGemmaModel(raw) ||
          info?.lane === "server_lan" ||
          (info?.provider_supported && !info?.local_download_supported)
        ) {
          return { key: "provider", label: "Server / LAN" };
        }
        return { key: "local", label: "Local" };
      }
      if (normalizedField === "rag_embedding_model") {
        return normalizedValue.startsWith("api:")
          ? { key: "api", label: "API" }
          : { key: "local", label: "Local" };
      }
    if (normalizedField === "rag_clip_model") {
      return { key: "local", label: "Local" };
    }
    if (normalizedField === "realtime_model" || normalizedField === "realtime_voice") {
      return { key: "api", label: "Cloud live" };
    }
    return null;
  };

  const describeModelProvider = (field, value) => {
    const normalizedField = String(field || "").trim().toLowerCase();
    const normalizedValue = String(value || "").trim().toLowerCase();
    if (!normalizedValue) return "";
    if (normalizedField === "transformer_model" && isDirectLocalGemmaModel(value)) {
      return "direct local";
    }
    if (normalizedField === "transformer_model" && isProviderFirstGemmaModel(value)) {
      return "provider/server lane";
    }
    if (normalizedField === "tts_model") {
      if (
        normalizedValue.startsWith("tts-") ||
        (normalizedValue.startsWith("gpt-4o") && normalizedValue.includes("tts"))
      ) {
        return "OpenAI API";
      }
      if (normalizedValue.includes("kitten") || normalizedValue.includes("kokoro")) {
        return "local engine";
      }
    }
    return "";
  };

  const getLaneDisplayLabel = (laneKey) => {
    if (laneKey === "api") return "API";
    if (laneKey === "provider") return "Server / LAN";
    return "Local";
  };

  const renderLaneSelector = (field, currentLaneKey, laneKeys, onSelect) => {
    const uniqueLaneKeys = Array.from(new Set((laneKeys || []).filter(Boolean)));
    if (!currentLaneKey || uniqueLaneKeys.length <= 1) return null;
    return (
      <div className="model-lane-switch" role="tablist" aria-label={`${field} model lanes`}>
        {uniqueLaneKeys.map((laneKey) => (
          <button
            key={`${field}-${laneKey}`}
            type="button"
            className={`model-lane-switch-btn model-lane-switch-btn--${laneKey}${
              currentLaneKey === laneKey ? " is-active" : ""
            }`}
            onClick={() => onSelect(laneKey)}
            aria-pressed={currentLaneKey === laneKey}
            title={`Show ${getLaneDisplayLabel(laneKey)} models`}
          >
            {getLaneDisplayLabel(laneKey)}
          </button>
        ))}
      </div>
    );
  };

  const getModelCapabilities = (field, value, info = null) => {
    const raw = String(value || "").trim();
    const normalizedField = String(field || "").trim().toLowerCase();
    const normalizedValue = raw.toLowerCase();
    if (!raw) return [];

    const capabilities = [];
    const pushCapability = (id, label) => {
      if (!capabilities.some((entry) => entry.id === id)) {
        capabilities.push({ id, label });
      }
    };
    const family = inferModelFamily(raw, info);
    const supportsImages =
      !!info?.supports_images ||
      normalizedValue.includes("paligemma") ||
      normalizedValue.includes("pixtral");

    if (normalizedField === "transformer_model") {
      pushCapability("text", "Text generation");
      if (supportsImages) {
        pushCapability("vision", "Image understanding");
      }
      if (
        languageToolingFamilies.has(family) ||
        isGemmaFamilyModel(raw) ||
        isGptOssModel(raw)
      ) {
        pushCapability("agentic", "Tool-aware chat");
      }
      return capabilities;
    }

    if (normalizedField === "stt_model") {
      pushCapability("speech", "Speech transcription");
      return capabilities;
    }

    if (normalizedField === "tts_model") {
      pushCapability("speech", "Speech synthesis");
      return capabilities;
    }

    if (normalizedField === "vision_model") {
      pushCapability("vision", "Image understanding");
      if (!normalizedValue.includes("clip")) {
        pushCapability("text", "Text output");
      }
      return capabilities;
    }

    if (normalizedField === "rag_embedding_model") {
      pushCapability("text", "Text embeddings");
      return capabilities;
    }

    if (normalizedField === "rag_clip_model") {
      pushCapability("vision", "Image embeddings");
      return capabilities;
    }

    if (normalizedField === "realtime_model") {
      pushCapability("live", "Live streaming");
      pushCapability("speech", "Live speech session");
      pushCapability("text", "Realtime responses");
      return capabilities;
    }

    if (normalizedField === "realtime_voice") {
      pushCapability("live", "Live streaming");
      pushCapability("speech", "Realtime voice output");
      return capabilities;
    }

    return capabilities;
  };

  const CapabilityGlyph = ({ id }) => {
    const svgProps = {
      viewBox: "0 0 20 20",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "1.7",
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
    };
    if (id === "speech") {
      return (
        <svg {...svgProps}>
          <path d="M10 3.5a2.5 2.5 0 0 1 2.5 2.5v4a2.5 2.5 0 1 1-5 0V6A2.5 2.5 0 0 1 10 3.5Z" />
          <path d="M5.5 9.5v.5a4.5 4.5 0 0 0 9 0v-.5" />
          <path d="M10 14.5v2.5" />
          <path d="M7.5 17h5" />
        </svg>
      );
    }
    if (id === "vision") {
      return (
        <svg {...svgProps}>
          <rect x="3.5" y="4.5" width="13" height="11" rx="2" />
          <circle cx="8" cy="8.25" r="1.1" />
          <path d="m5.5 13.5 3.2-3.4 2.1 2.2 1.8-1.7 2.4 2.9" />
        </svg>
      );
    }
    if (id === "live") {
      return (
        <svg {...svgProps}>
          <path d="M4.5 10h2.25l1.6-3 2.2 6 1.55-3h3.4" />
          <path d="M4 5.5a8 8 0 0 1 0 9" />
          <path d="M16 5.5a8 8 0 0 1 0 9" />
        </svg>
      );
    }
    if (id === "agentic") {
      return (
        <svg {...svgProps}>
          <circle cx="10" cy="10" r="2.2" />
          <circle cx="10" cy="4.2" r="1.2" />
          <circle cx="14.8" cy="12.8" r="1.2" />
          <circle cx="5.2" cy="12.8" r="1.2" />
          <path d="M10 5.4v2.4" />
          <path d="m13.8 12.1-1.9-1.1" />
          <path d="m6.2 12.1 1.9-1.1" />
        </svg>
      );
    }
    return (
      <svg {...svgProps}>
        <path d="M5 5h10" />
        <path d="M5 10h10" />
        <path d="M5 15h7" />
      </svg>
    );
  };

  const renderCapabilityStrip = (field, value, info = null) => {
    const capabilities = getModelCapabilities(field, value, info);
    if (capabilities.length === 0) return null;
    return (
      <div className="model-capability-strip" aria-label="Model capabilities">
        {capabilities.map((capability) => (
          <span
            key={`${field}-${capability.id}`}
            className="model-capability-chip"
            title={capability.label}
            aria-label={capability.label}
          >
            <CapabilityGlyph id={capability.id} />
          </span>
        ))}
      </div>
    );
  };

  const openAiVoiceOptions = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "marin",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "cedar",
  ];
  const openAiLegacyTtsVoiceOptions = [
    "alloy",
    "ash",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
  ];
  const realtimeModelOptions = [
    "gpt-realtime-2.1",
    "gpt-realtime-2.1-mini",
    "gpt-realtime-2",
    "gpt-realtime-mini",
    "gpt-realtime-1.5",
    "gpt-realtime",
  ];
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
  const liveBridgeSuggestedModels = [
    "gpt-oss-20b",
    "gemma-4-E2B-it",
    "gemma-4-E4B-it",
    "gemma-4-12B-it-qat-q4_0-gguf",
    "gemma-4-12B-it",
    "gemma-4-26B-A4B-it",
    "gemma-4-31B-it",
    "pixtral-12b-2409",
  ];
  const liveBridgeModelOptions = useMemo(() => {
    const seen = new Set();
    const options = [];
    const add = (value) => {
      const model = typeof value === "string" ? value.trim() : "";
      if (!model || seen.has(model)) return;
      seen.add(model);
      options.push(model);
    };
    liveBridgeSuggestedModels.forEach(add);
    (providerModelOptions || []).forEach(add);
    (registeredLocalModels || []).forEach((entry) => add(entry?.alias));
    filterAvailableModelsForField("transformer_model", availableModels, {
      includeAll: includeCacheUnfiltered,
    }).forEach(add);
    add(settings.live_agent_model);
    add(settings.live_multimodal_model);
    add(settings.local_provider_preferred_model);
    return options;
  }, [
    availableModels,
    includeCacheUnfiltered,
    providerModelOptions,
    registeredLocalModels,
    settings.live_agent_model,
    settings.live_multimodal_model,
    settings.local_provider_preferred_model,
  ]);
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

  const voicePresetLooksLikeSpeechModel = (() => {
    const value = String(settings.voice_model || "").trim().toLowerCase();
    if (!value) return false;
    return [
      "voxtral",
      "whisper",
      "wav2vec",
      "transcribe",
      "gemma-",
      "gpt-realtime",
      "llama",
      "mistral",
      "qwen",
    ].some((needle) => value.includes(needle));
  })();

  const realtimeVoiceIsKnown =
    !settings.realtime_voice ||
    realtimeVoiceOptions.includes(settings.realtime_voice);
  const normalizedStreamBackend = String(settings.stream_backend || "api")
    .trim()
    .toLowerCase();
  const liveStreamingLaneKey = normalizedStreamBackend === "api" ? "api" : "local";
  const localLiveTransportValue =
    normalizedStreamBackend === "livekit" ? "livekit" : "local";
  const liveStreamingCapabilityField =
    liveStreamingLaneKey === "api" ? "realtime_model" : "live_agent_model";
  const liveStreamingCapabilityValue =
    liveStreamingLaneKey === "api"
      ? settings.realtime_model
      : settings.live_agent_model;

  const ragEmbeddingPresets = [
    { label: "Hash fallback (local)", value: "simple" },
    { label: "Sentence Transformers · all-MiniLM-L6-v2", value: "local:all-MiniLM-L6-v2" },
    { label: "Sentence Transformers - all-mpnet-base-v2", value: "local:all-mpnet-base-v2" },
    { label: "EmbeddingGemma 300M (local review)", value: "local:google/embeddinggemma-300M" },
    { label: "OpenAI text-embedding-3-small (API)", value: "api:text-embedding-3-small" },
    { label: "OpenAI text-embedding-3-large (API)", value: "api:text-embedding-3-large" },
  ];
  const embeddingLaneKey =
    getModelLaneMeta("rag_embedding_model", settings.rag_embedding_model)?.key || "local";
  const ragEmbeddingLaneOptions = Array.from(
    new Set(
      ragEmbeddingPresets.map(
        (preset) => getModelLaneMeta("rag_embedding_model", preset.value)?.key || "local",
      ),
    ),
  );
  const visibleRagEmbeddingPresets =
    ragEmbeddingLaneOptions.length > 1
      ? ragEmbeddingPresets.filter(
          (preset) =>
            (getModelLaneMeta("rag_embedding_model", preset.value)?.key || "local") ===
            embeddingLaneKey,
        )
      : ragEmbeddingPresets;

  const ragClipPresets = [
    { label: "OpenCLIP ViT-B-32 (recommended)", value: "ViT-B-32" },
    { label: "OpenCLIP ViT-B-16", value: "ViT-B-16" },
    { label: "OpenCLIP ViT-L-14", value: "ViT-L-14" },
  ];

  const suggestedVisionModels = [
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

          static_model: data.static_model || "gpt-5.4-mini",

          harmony_format: data.harmony_format ?? false,
          harmony_format_mode: normalizeHarmonyFormatMode(
            data.harmony_format_mode ??
              (typeof data.harmony_format === "boolean"
                ? data.harmony_format
                  ? "enabled"
                  : "disabled"
                : state.harmonyFormatMode),
          ),

          server_url: data.server_url || "",
          server_preset_id: data.server_preset_id || "",
          server_presets: normalizeServerPresets(data.server_presets),

          stt_model: data.stt_model || "whisper-1",

          tts_model: data.tts_model || "tts-1",

          // Default to a valid OpenAI TTS voice name

          voice_model: data.voice_model || "alloy",
          stream_backend: data.stream_backend || "api",
          realtime_model: data.realtime_model || "gpt-realtime-2.1",
          realtime_voice: data.realtime_voice || "alloy",
          live_agent_mode: data.live_agent_mode || "local",
          live_agent_model: data.live_agent_model || "",
          live_multimodal_model: data.live_multimodal_model || "",
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
              : 0.45,
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
          background_autonomy_enabled:
            typeof data.background_autonomy_enabled === "boolean"
              ? data.background_autonomy_enabled
              : false,
          background_autonomy_sandbox_processes:
            typeof data.background_autonomy_sandbox_processes === "boolean"
              ? data.background_autonomy_sandbox_processes
              : true,
          background_autonomy_mode: normalizeBackgroundAutonomyMode(
            data.background_autonomy_mode,
          ),
          background_autonomy_interval_seconds:
            typeof data.background_autonomy_interval_seconds === "number"
              ? data.background_autonomy_interval_seconds
              : 900,
          background_autonomy_max_reflections_per_tick:
            typeof data.background_autonomy_max_reflections_per_tick === "number"
              ? data.background_autonomy_max_reflections_per_tick
              : 1,
          background_autonomy_max_runtime_seconds:
            typeof data.background_autonomy_max_runtime_seconds === "number"
              ? data.background_autonomy_max_runtime_seconds
              : 1800,
          background_autonomy_satisfied_threshold:
            typeof data.background_autonomy_satisfied_threshold === "number"
              ? Math.min(1, Math.max(0, data.background_autonomy_satisfied_threshold))
              : 0.8,
          background_autonomy_basic_tick_count:
            typeof data.background_autonomy_basic_tick_count === "number"
              ? data.background_autonomy_basic_tick_count
              : 2,
          background_autonomy_basic_tick_seconds:
            typeof data.background_autonomy_basic_tick_seconds === "number"
              ? data.background_autonomy_basic_tick_seconds
              : 300,
          background_autonomy_min_priority:
            typeof data.background_autonomy_min_priority === "number"
              ? Math.min(1, Math.max(0, data.background_autonomy_min_priority))
              : 0.05,

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
        setInitialComparable(buildComparable(newSettings, false, false));
        setInitialized(true);

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
          if (typeof newSettings.rag_embedding_model === "string") {
            next.ragEmbeddingModel = newSettings.rag_embedding_model;
          }
          if (typeof newSettings.rag_clip_model === "string") {
            next.ragClipModel = newSettings.rag_clip_model;
          }
          return next;
        });

        // default to not using custom folders; user can opt-in via checkbox

        setUseCustomModelsFolder(false);

        setUseCustomConvFolder(false);

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

  const handleToolPolicyChange = async (toolId, field, value) => {
    const normalizedId = String(toolId || "").trim();
    if (!normalizedId) return;
    const currentEntry = (toolCatalog || []).find(
      (entry) => String(entry?.id || "") === normalizedId,
    );
    const currentPolicy = normalizeToolPolicy(currentEntry?.policy);
    const nextPolicy = {
      workflow:
        field === "workflow"
          ? normalizeToolWorkflow(value, currentPolicy.workflow)
          : currentPolicy.workflow,
      approval:
        field === "approval"
          ? normalizeToolApproval(value, currentPolicy.approval)
          : currentPolicy.approval,
    };
    const nextPolicyPayload = {
      ...currentPolicy,
      ...nextPolicy,
      workflows: {
        text: ["text", "both"].includes(nextPolicy.workflow),
        live: ["live", "both"].includes(nextPolicy.workflow),
      },
      overridden: true,
    };
    setToolCatalog((prev) =>
      (prev || []).map((entry) =>
        String(entry?.id || "") === normalizedId
          ? { ...entry, policy: nextPolicyPayload }
          : entry,
      ),
    );
    setToolPolicySaving(`${normalizedId}:${field}`);
    setToolPolicyMessage("");
    try {
      await axios.post("/api/user-settings", {
        tool_policies: {
          [normalizedId]: nextPolicy,
        },
      });
      setToolPolicyMessage(
        `${currentEntry?.display_name || normalizedId} tool policy saved.`,
      );
      refreshToolCatalog();
    } catch (err) {
      setToolPolicyMessage("Failed to save tool policy.");
    } finally {
      setToolPolicySaving("");
    }
  };



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

  const refreshProviderRuntime = async (quiet = false, { refresh = false } = {}) => {
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
      const response = await axios.get(
        quiet ? "/api/llm/provider/status" : "/api/llm/provider/models",
        {
          params: quiet
            ? { provider: providerKey, quick: true }
            : refresh
              ? { provider: providerKey, refresh: true }
              : { provider: providerKey },
        },
      );
      setProviderRuntime(response?.data?.runtime || null);
      if (!quiet) {
        setProviderModelOptions(filterChatCapableProviderModels(response?.data?.models));
      }
      setProviderRuntimeError("");
    } catch (err) {
      setProviderRuntime(null);
      if (!quiet) {
        setProviderModelOptions([]);
      }
      setProviderRuntimeError(
        err?.response?.data?.detail || "Provider runtime is not reachable right now.",
      );
    } finally {
      if (!quiet) {
        setProviderRuntimeLoading(false);
      }
    }
  };

  const refreshServerRuntime = useCallback(async ({ refresh = false } = {}) => {
    const serverUrl =
      typeof settings.server_url === "string" ? settings.server_url.trim() : "";
    if (settings.mode !== "server" || !serverUrl) {
      setServerRuntime(null);
      setServerRuntimeError("");
      setServerRuntimeLoading(false);
      return;
    }
    setServerRuntimeLoading(true);
    setServerRuntimeError("");
    try {
      const response = await axios.get("/api/llm/server/models", {
        params: {
          server_url: serverUrl,
          ...(settings.server_preset_id
            ? { preset_id: settings.server_preset_id }
            : {}),
          ...(refresh ? { refresh: true } : {}),
        },
      });
      setServerRuntime(response?.data || { reachable: false, models: [] });
    } catch (err) {
      void err;
      setServerRuntime({ reachable: false, models: [] });
      setServerRuntimeError("Server/LAN endpoint is not reachable right now.");
    } finally {
      setServerRuntimeLoading(false);
    }
  }, [settings.mode, settings.server_preset_id, settings.server_url]);

  const refreshLocalRuntime = async (quiet = false) => {
    const shouldInspect =
      settings.mode === "local" &&
      settings.transformer_model &&
      !isLocalRuntimeEntry(settings.transformer_model);
    if (!shouldInspect) {
      setLocalRuntime(null);
      setLocalRuntimeError("");
      setLocalRuntimeLoading(false);
      return;
    }
    if (!quiet) {
      setLocalRuntimeLoading(true);
    }
    try {
      const response = await axios.get("/api/llm/local-status", {
        params: {
          model: settings.transformer_model,
          quick: true,
        },
      });
      setLocalRuntime(response?.data?.runtime || null);
      setLocalRuntimeError("");
    } catch (err) {
      setLocalRuntime(null);
      setLocalRuntimeError(
        err?.response?.data?.detail || "Local runtime is not reachable right now.",
      );
    } finally {
      if (!quiet) {
        setLocalRuntimeLoading(false);
      }
    }
  };

  const runLocalRuntimeAction = async (action) => {
    const normalizedAction = String(action || "").trim().toLowerCase();
    if (!normalizedAction || !settings.transformer_model) return;
    if (normalizedAction === "load" && localRuntimeLoadBlockedReason) {
      setLocalRuntimeMessage("");
      setLocalRuntimeError(localRuntimeLoadBlockedReason);
      return;
    }
    setLocalRuntimeActionBusy(normalizedAction);
    setLocalRuntimeMessage("");
    setLocalRuntimeError("");
    try {
      if (normalizedAction === "load") {
        await axios.post("/api/llm/load-local", {
          model: settings.transformer_model,
        });
      } else if (normalizedAction === "unload") {
        await axios.post("/api/llm/unload-local");
      } else {
        return;
      }
      await refreshLocalRuntime();
      setLocalRuntimeMessage(
        normalizedAction === "load"
          ? "Language runtime load requested."
          : "Language runtime unloaded.",
      );
    } catch (err) {
      setLocalRuntimeError(
        err?.response?.data?.detail || "Language runtime action failed.",
      );
    } finally {
      setLocalRuntimeActionBusy("");
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
      await refreshProviderRuntime(false, { refresh: true });
    } catch (err) {
      setProviderActionMessage(
        err?.response?.data?.detail || "Provider action failed.",
      );
    } finally {
      setProviderActionBusy("");
    }
  };

  const runEmbeddingRuntimeAction = async (action) => {
    const normalizedAction = String(action || "").trim().toLowerCase();
    if (!normalizedAction) return;
    setEmbeddingRuntimeBusy(normalizedAction);
    setEmbeddingRuntimeMessage("");
    try {
      const response = await axios.post(`/api/rag/embeddings/${normalizedAction}`);
      const detail =
        response?.data?.embedding_runtime?.state === "loaded"
          ? "Embedding runtime loaded."
          : response?.data?.embedding_runtime?.state === "idle"
            ? "Embedding runtime unloaded."
            : "Embedding runtime updated.";
      setEmbeddingRuntimeMessage(detail);
      await refreshStatus();
    } catch (err) {
      setEmbeddingRuntimeMessage(
        err?.response?.data?.detail || "Embedding runtime action failed.",
      );
    } finally {
      setEmbeddingRuntimeBusy("");
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

  const renderStatusSection = () => {

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
    const embeddingRuntime =
      ragStatus && typeof ragStatus.embedding_runtime === "object"
        ? ragStatus.embedding_runtime
        : null;
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
    const rawWsStatus = normalizeStatus(svcWs);
    const wsLastActivityAt = Math.max(
      Number(state.wsLastEventAt || 0),
      Number(state.wsLastErrorAt || 0),
    );
    const wsRecentlyActive =
      rawWsStatus === "offline" &&
      wsLastActivityAt > 0 &&
      runtimeNow - wsLastActivityAt < 30000;
    const normalizedWsStatus = wsRecentlyActive ? "loading" : rawWsStatus;
    const wsStatusSummary =
      normalizedWsStatus === "online"
        ? "Live thought stream connected."
        : normalizedWsStatus === "loading"
          ? wsRecentlyActive
            ? "Reconnecting to live thought stream."
            : "Connecting to live thought stream."
          : "Live thought stream not connected.";
    const celeryStatusNormalized = normalizeStatus(svcCelery);
    const celeryNoticeTitle =
      celeryStatusNormalized === "offline"
        ? "Background queue unavailable"
        : celeryStatusNormalized === "degraded"
          ? "Background queue degraded"
          : celeryStatusNormalized === "loading"
            ? "Checking background queue"
            : "";
    const celeryNoticeBody =
      celeryStatusNormalized === "offline"
        ? "Background jobs will not start until a Celery worker responds."
        : celeryStatusNormalized === "degraded"
          ? "Some workers did not respond during the last check."
          : celeryStatusNormalized === "loading"
            ? "Fetching worker and task state."
            : "";
    const celeryEmptyLabel =
      celeryStatusNormalized === "offline"
        ? "Queue unavailable"
        : celeryLoading
          ? "Loading tasks"
          : "No tasks in this view";
    const celeryHasTasks = celeryTasks.length > 0;
    const showCeleryOperations =
      celeryStatusNormalized !== "offline" ||
      celeryHasTasks ||
      Boolean(celeryError) ||
      showFailures;
    const autonomy = backgroundAutonomyStatus || {};
    const autonomySession =
      autonomy && typeof autonomy.session === "object" ? autonomy.session : {};
    const autonomyMode = normalizeBackgroundAutonomyMode(
      autonomy.configured_mode || autonomy.mode || settings.background_autonomy_mode,
    );
    const autonomyRuntimeMinutes = Math.max(
      1,
      Math.round(
        Number(
          autonomy.max_runtime_seconds ||
            settings.background_autonomy_max_runtime_seconds ||
            1800,
        ) / 60,
      ),
    );
    const autonomyState = backgroundAutonomyLoading
      ? "loading"
      : autonomy.error
        ? "offline"
        : autonomy.routine_enabled
          ? "online"
          : autonomy.enabled
            ? "degraded"
            : "offline";
    const autonomySummary = autonomy.error
      ? "Autonomy status unavailable."
      : autonomy.routine_enabled
        ? `${autonomyMode.replace("_", " ")} mode, ${autonomyRuntimeMinutes} minute budget.`
        : autonomy.enabled
          ? "Configured but not running in routine mode."
          : "Manual until enabled.";
    const autonomyStopReason = autonomySession.stop_reason
      ? String(autonomySession.stop_reason).replace(/_/g, " ")
      : "";
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

        <p className="status-summary-copy" role="status">
          API, websocket, background queue, provider bridge, and storage health.
        </p>

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

            {renderStatusDot(normalizedWsStatus)}

            <div>

              <div className="status-label">WebSocket</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(normalizedWsStatus)}

                <span
                  className={`status-note status-note--primary ${
                    normalizedWsStatus === "online" ? "" : "warn"
                  }`}
                >
                  {wsStatusSummary}
                </span>

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

          <div className="status-item" title="Background autonomy supervisor">

            {renderStatusDot(autonomyState)}

            <div>

              <div className="status-label">Autonomy</div>

              <div className="status-sub status-sub--stacked">

                {renderStatusBadge(autonomyState)}

                <span className={`status-note ${autonomyState === "offline" ? "warn" : ""}`}>

                  {autonomySummary}

                </span>

                {autonomyStopReason && (

                  <span className="status-note">stopped: {autonomyStopReason}</span>

                )}

              </div>

            </div>

          </div>

        </div>

        <div className="celery-panel">

          <div className="celery-header">

            <div
              className="celery-title-row"
            >

              {renderStatusDot(svcCelery)}

              <h3>Background jobs</h3>

              {renderStatusBadge(svcCelery)}

              <SettingsInfoTip
                label="About background jobs"
                text={`Optional workers handle scheduled and long-running jobs; normal chat still works while they are offline.${
                  svcCeleryNote ? ` Current status: ${svcCeleryNote}.` : ""
                }`}
              />

            </div>

            <div className="celery-toolbar-controls">

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

              <label className="settings-toggle-row settings-toggle-row--compact">

                <input

                  type="checkbox"

                  checked={celeryAuto}

                  onChange={(e) => setCeleryAuto(!!e.target.checked)}

                />

                <span>Auto-refresh</span>

              </label>

              <button type="button" onClick={() => refreshCeleryTasks()} disabled={celeryLoading}>

                {celeryLoading ? 'Refreshing…' : 'Refresh'}

              </button>

            </div>

          </div>

            {celeryNoticeTitle && (
              <div
                className={`celery-notice celery-notice--${celeryStatusNormalized}`}
                role="status"
              >
                <strong>{celeryNoticeTitle}</strong>
                <SettingsInfoTip label={celeryNoticeTitle} text={celeryNoticeBody} />
              </div>
            )}

            {showCeleryOperations && (
              <>
            <div className="celery-action-row">

              <div className="celery-action-group">

                <label className="celery-control-label" title="Queue name to purge">
                  Queue
                </label>

                <input

                  className="celery-input celery-input--queue"

                  type="text"

                  value={purgeQueue}

                  onChange={(e) => setPurgeQueue(e.target.value)}

                  placeholder="celery"

                />

                <label
                  className="inline-flex celery-check"
                  style={{ gap: 6 }}
                  title="Terminate running tasks too"
                >

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

              <div className="celery-action-group celery-action-group--retry">

                <label className="celery-control-label" title="Retry a task by name">
                  Retry name
                </label>

                <input

                  className="celery-input celery-input--retry"

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

                      <td colSpan={7} className="celery-empty">

                        {celeryEmptyLabel}

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

              </>
            )}

            <ModelJobsPanel />

          </div>

        </div>

    );

  };



  useEffect(() => {
    if (loading || activeSettingsSection !== "models") return;
    fetchAvailableModels(useCustomModelsFolder ? settings.models_folder : undefined);
  }, [
    activeSettingsSection,
    includeCacheUnfiltered,
    loading,
    settings.models_folder,
    useCustomModelsFolder,
  ]);

  useEffect(() => {
    if (loading) return;
    refreshProviderRuntime();
  }, [loading, settings.mode, settings.local_provider]);

  useEffect(() => {
    if (loading) return;
    refreshServerRuntime();
  }, [loading, refreshServerRuntime]);

  useEffect(() => {
    const providerKey = normalizeModelId(settings.local_provider) || "lmstudio";
    if (!(settings.mode === "local" && MANAGED_LOCAL_PROVIDERS.has(providerKey))) {
      return undefined;
    }
    const id = setInterval(() => {
      refreshProviderRuntime(true);
    }, PROVIDER_RUNTIME_STATUS_POLL_MS);
    return () => clearInterval(id);
  }, [settings.mode, settings.local_provider]);

  useEffect(() => {
    if (loading) return;
    refreshLocalRuntime();
  }, [loading, settings.mode, settings.transformer_model]);

  useEffect(() => {
    const shouldPoll =
      settings.mode === "local" &&
      settings.transformer_model &&
      !isLocalRuntimeEntry(settings.transformer_model);
    if (!shouldPoll) {
      return undefined;
    }
    const id = setInterval(() => {
      refreshLocalRuntime(true);
    }, 20000);
    return () => clearInterval(id);
  }, [settings.mode, settings.transformer_model]);



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
      const catalogModel = resolveLocalCatalogModelId(model);

      if (!model) return;

      axios

        .get(`/api/models/info/${encodeURIComponent(catalogModel)}`)

        .then((r) => {

          setModelInfos((prev) => ({ ...prev, [field]: r.data }));

          const repo = String(r.data?.repo_id || "");
          const dl =
            typeof r.data?.downloadable === "boolean"
              ? r.data.downloadable
              : !!repo && !repo.startsWith("TODO");

          setModelDownloadable((prev) => ({ ...prev, [field]: dl }));

        })

        .catch(() => {

          setModelInfos((prev) => ({ ...prev, [field]: { size: 0 } }));

          // If info lookup fails, still allow download attempt for known downloadable models.
          const fallbackDl = isKnownDownloadableModel(catalogModel);

          setModelDownloadable((prev) => ({ ...prev, [field]: fallbackDl }));

        });

      axios

        .get(`/api/models/exists/${encodeURIComponent(catalogModel)}`,

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

          `/api/models/local-size/${encodeURIComponent(catalogModel)}`,

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

          `/api/models/verify/${encodeURIComponent(catalogModel)}`,

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
  }, [
    state.captureAllowModelRawImageAccess,
    state.captureAllowSummaryFallback,
    state.captureDefaultSensitivity,
    state.captureRetentionDays,
  ]);



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
        if (typeof s.privacy_filter_mode === "string") {
          setPrivacyFilterMode(normalizePrivacyFilterMode(s.privacy_filter_mode));
        }
        if (typeof s.privacy_filter_model === "string" && s.privacy_filter_model.trim()) {
          setPrivacyFilterModel(s.privacy_filter_model.trim());
        }
        if (typeof s.privacy_filter_route_private_mode === "string") {
          setPrivacyRouteMode(
            normalizePrivacyRouteMode(s.privacy_filter_route_private_mode),
          );
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



  const commitSettingValue = useCallback((name, nextValue) => {
    setSettings((prev) => {
      const next = {
        ...prev,
        [name]: nextValue,
      };
      if (name === "transformer_model" && isLocalRuntimeEntry(nextValue)) {
        next.local_provider = normalizeModelId(nextValue);
      }
      if (name === "harmony_format") {
        next.harmony_format_mode = nextValue ? "enabled" : "disabled";
      }
      if (name === "tts_model") {
        const normalizedTts = String(nextValue || "").trim().toLowerCase();
        let compatibleVoices = voicePresetOptions;
        if (normalizedTts === "tts-1" || normalizedTts === "tts-1-hd") {
          compatibleVoices = openAiLegacyTtsVoiceOptions;
        } else if (normalizedTts.startsWith("gpt-4o") && normalizedTts.includes("tts")) {
          compatibleVoices = openAiVoiceOptions;
        } else if (normalizedTts.includes("kitten")) {
          compatibleVoices = kittenVoiceOptions;
        } else if (normalizedTts.includes("kokoro")) {
          compatibleVoices = kokoroVoiceOptions;
        }
        if (
          compatibleVoices.length > 0 &&
          !compatibleVoices.includes(String(prev.voice_model || "").trim())
        ) {
          next.voice_model = compatibleVoices[0];
        }
      }
      if (name === "local_provider") {
        const normalized = normalizeModelId(nextValue);
        if (isLocalRuntimeEntry(prev.transformer_model)) {
          next.transformer_model = normalized;
        }
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

  }, [
    kittenVoiceOptions,
    kokoroVoiceOptions,
    openAiLegacyTtsVoiceOptions,
    openAiVoiceOptions,
    voicePresetOptions,
  ]);

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

    commitSettingValue(name, nextValue);

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

  const buildGlobalSelectionPatch = (prevState, nextSettings) => {
    const patch = {};
    if (nextSettings.model && nextSettings.model !== prevState.apiModel) {
      patch.apiModel = nextSettings.model;
    }
    const selectedLanguageModel =
      typeof nextSettings.transformer_model === "string"
        ? nextSettings.transformer_model.trim()
        : "";
    if (nextSettings.mode === "local" && selectedLanguageModel) {
      if (selectedLanguageModel !== prevState.localModel) {
        patch.localModel = selectedLanguageModel;
      }
      if (
        !isLocalRuntimeEntry(selectedLanguageModel) &&
        selectedLanguageModel !== prevState.transformerModel
      ) {
        patch.transformerModel = selectedLanguageModel;
      }
    }
    return patch;
  };

  useEffect(() => {
    const next = buildGlobalSelectionPatch(state, settings);
    if (Object.keys(next).length > 0) {
      setState((prev) => ({ ...prev, ...next }));
    }
  }, [
    settings.mode,
    settings.model,
    settings.transformer_model,
    state.apiModel,
    state.localModel,
    state.transformerModel,
    setState,
  ]);

  useEffect(() => {
    if (settings.mode !== "local") return;
    const selectedLocalModel =
      typeof state.localModel === "string" ? state.localModel.trim() : "";
    if (!selectedLocalModel || selectedLocalModel === settings.transformer_model) {
      return;
    }
    setSettings((prev) => {
      if (prev.mode !== "local" || prev.transformer_model === selectedLocalModel) {
        return prev;
      }
      const next = {
        ...prev,
        transformer_model: selectedLocalModel,
      };
      if (isLocalRuntimeEntry(selectedLocalModel)) {
        next.local_provider = normalizeModelId(selectedLocalModel);
      }
      return next;
    });
  }, [settings.mode, settings.transformer_model, state.localModel]);

  useEffect(() => {
    if (settings.mode !== "server") {
      lastServerModelSyncRef.current = undefined;
      return;
    }
    const selectedServerModel =
      typeof state.transformerModel === "string" ? state.transformerModel.trim() : "";
    if (lastServerModelSyncRef.current === selectedServerModel) {
      return;
    }
    lastServerModelSyncRef.current = selectedServerModel;
    if (selectedServerModel === settings.transformer_model) {
      return;
    }
    setSettings((prev) => {
      if (prev.mode !== "server" || prev.transformer_model === selectedServerModel) {
        return prev;
      }
      return {
        ...prev,
        transformer_model: selectedServerModel,
      };
    });
  }, [settings.mode, settings.transformer_model, state.transformerModel]);

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

  const providerCapabilities =
    providerRuntime?.capabilities && typeof providerRuntime.capabilities === "object"
      ? providerRuntime.capabilities
      : {};
  const providerExternalEndpointMode = providerCapabilities.start_stop === false;
  const providerEndpointReachable =
    !!providerRuntime?.server_running ||
    (providerExternalEndpointMode && providerModelOptions.length > 0);
  const providerCliName =
    selectedProviderKey === "ollama"
      ? "Ollama CLI (ollama)"
      : selectedProviderKey === "lmstudio"
        ? "LM Studio CLI (lms)"
        : "";
  const providerCliMissing =
    providerRuntimeInspectable &&
    !!providerCliName &&
    !providerExternalEndpointMode &&
    providerRuntime?.installed === false;
  const providerRuntimeLastError =
    typeof providerRuntime?.last_error === "string"
      ? providerRuntime.last_error.trim()
      : "";
  const providerRuntimeDisplayError =
    providerExternalEndpointMode &&
    /remote load endpoint is unavailable/i.test(providerRuntimeLastError)
      ? providerRuntimeError || ""
      : providerRuntimeError || providerRuntimeLastError;
  const providerLabelText =
    formatLocalRuntimeLabel(selectedProviderKey) || selectedProviderKey || "provider";
  const providerServerOwnershipKnown =
    typeof providerRuntime?.server_owned_by_float === "boolean";
  const providerServerOwnedByFloat = providerServerOwnershipKnown
    ? providerRuntime.server_owned_by_float
    : true;
  const providerLoadedModelOwnershipKnown =
    typeof providerRuntime?.loaded_model_owned_by_float === "boolean";
  const providerLoadedModelOwnedByFloat = providerLoadedModelOwnershipKnown
    ? providerRuntime.loaded_model_owned_by_float
    : providerServerOwnedByFloat;
  const providerExternalManagedServer =
    !providerExternalEndpointMode &&
    !!providerRuntime?.server_running &&
    providerServerOwnershipKnown &&
    !providerServerOwnedByFloat;
  const providerExternalManagedLoadedModel =
    !!providerRuntime?.loaded_model &&
    providerLoadedModelOwnershipKnown &&
    !providerLoadedModelOwnedByFloat;
  const providerOwnershipGuidance =
    providerExternalManagedServer || providerExternalManagedLoadedModel
      ? `${providerLabelText} is already running outside Float${
          providerRuntime?.base_url ? ` at ${providerRuntime.base_url}` : ""
        }. Stop it in the external app or switch this lane to External HTTP only before using start, stop, load, unload, or delete here.`
      : "";
  const providerCliWarning = providerCliMissing
    ? providerExternalManagedServer
      ? `${providerCliName} was not detected on this system PATH${
          selectedProviderKey === "lmstudio" && settings.lmstudio_path
            ? " or configured LM Studio CLI path"
            : ""
        }. Float can inspect the already-running ${providerLabelText} server${
          providerRuntime?.base_url ? ` at ${providerRuntime.base_url}` : ""
        }, but process control stays in the external app until this lane is switched to External HTTP only.`
      : `${providerCliName} was not detected on this system PATH${
          selectedProviderKey === "lmstudio" && settings.lmstudio_path
            ? " or configured LM Studio CLI path"
            : ""
        }. Float cannot start, stop, or stream logs for this provider until it is available.`
    : "";
  const providerRuntimeStatus = !providerRuntimeInspectable
    ? "offline"
    : providerRuntimeLoading && !providerRuntime
      ? "loading"
      : providerRuntimeDisplayError
        ? "offline"
        : providerExternalManagedServer || providerExternalManagedLoadedModel
          ? "degraded"
        : providerCliMissing
          ? "degraded"
        : providerExternalEndpointMode
          ? providerEndpointReachable
            ? "online"
            : "offline"
          : providerRuntime?.server_running
            ? providerRuntime?.model_loaded
              ? "online"
              : "degraded"
            : "offline";
  const providerRuntimeCheckedAgo = providerRuntime?.checked_at
    ? formatRelativeTime(providerRuntime.checked_at, runtimeNow)
    : null;
  const providerRuntimeCheckedClock = formatClockTime(providerRuntime?.checked_at);
  const localRuntimePreflight =
    localRuntime?.preflight && typeof localRuntime.preflight === "object"
      ? localRuntime.preflight
      : null;
  const localRuntimeLoadBlockedReason =
    localRuntimePreflight && localRuntimePreflight.ready === false
      ? String(
          localRuntimePreflight.hint ||
            "Direct local runtime is not ready yet.",
        ).trim()
      : "";
  const localRuntimeLoadDisabled =
    !settings.transformer_model ||
    !!localRuntimeLoading ||
    localRuntimeActionBusy === "load" ||
    !!localRuntimeLoadBlockedReason;
  const localRuntimeTiming = (() => {
    const loadState = String(localRuntime?.load_state || "").trim().toLowerCase();
    const startedAgo = localRuntime?.load_started_at
      ? formatRelativeTime(localRuntime.load_started_at, runtimeNow)
      : null;
    const finishedAgo = localRuntime?.load_finished_at
      ? formatRelativeTime(localRuntime.load_finished_at, runtimeNow)
      : null;
    if (loadState === "loading" && startedAgo) {
      return `Loading started ${startedAgo}.`;
    }
    if (loadState === "ready" && finishedAgo) {
      return `Loaded ${finishedAgo}.`;
    }
    if (loadState === "error" && finishedAgo) {
      return `Failed ${finishedAgo}.`;
    }
    return null;
  })();
  const providerTargetModel =
    (
      (typeof providerRuntime?.effective_model === "string"
        ? providerRuntime.effective_model
        : "") ||
      (typeof providerRuntime?.preferred_model === "string"
        ? providerRuntime.preferred_model
        : "") ||
      settings.local_provider_preferred_model ||
      ""
    )
      .toString()
      .trim();
  const providerRuntimeSummary = providerRuntime?.loaded_model
    ? providerExternalManagedLoadedModel
      ? isChatCapableProviderModelName(providerRuntime.loaded_model)
        ? `Loaded outside Float: ${providerRuntime.loaded_model}`
        : `Loaded non-chat model outside Float: ${providerRuntime.loaded_model}`
      : isChatCapableProviderModelName(providerRuntime.loaded_model)
        ? `Loaded: ${providerRuntime.loaded_model}`
        : `Loaded non-chat model: ${providerRuntime.loaded_model}`
    : providerExternalEndpointMode
      ? providerEndpointReachable
        ? providerTargetModel
          ? `Endpoint reachable. Target: ${providerTargetModel}.`
          : "Endpoint reachable."
        : "Endpoint is not reachable."
      : providerExternalManagedServer
        ? "Server is running outside Float."
      : providerRuntime?.server_running
        ? "Server is running without a model loaded."
        : "Server is not running.";
  const providerRuntimeDetail = !providerRuntimeInspectable
    ? "Inventory polling is available for LM Studio, Ollama, or a custom OpenAI-compatible endpoint."
    : providerRuntimeDisplayError
      ? providerRuntimeDisplayError
      : providerOwnershipGuidance
        ? providerOwnershipGuidance
      : providerRuntime?.loaded_model &&
          !isChatCapableProviderModelName(providerRuntime.loaded_model)
        ? `${providerRuntime.loaded_model} looks like an embedding model. Load a language model here for chat requests.`
      : providerExternalEndpointMode
        ? providerModelOptions.length > 0
          ? `${providerModelOptions.length} provider models reported. External HTTP mode keeps start, stop, and model loading outside Float.`
          : "External HTTP mode reports endpoint reachability here and leaves process control outside Float."
      : providerModelOptions.length > 0
        ? `${providerModelOptions.length} provider models reported.`
        : providerRuntimeLoading
          ? "Checking provider runtime..."
          : "No provider models reported yet.";
  const providerRuntimeFreshnessTooltip = providerRuntimeCheckedAgo
    ? `Inventory checked ${providerRuntimeCheckedAgo}${
        providerRuntimeCheckedClock ? ` (${providerRuntimeCheckedClock})` : ""
      }. Automatic provider refresh runs about once per minute.`
    : "Inventory has not been checked yet.";
  const providerRuntimeLastOperation = formatProviderLastOperation(
    providerRuntime?.last_operation,
    runtimeNow,
  );
  const languageProviderRuntime = providerRuntime
    ? {
        ...providerRuntime,
        ...(providerRuntime.loaded_model &&
        typeof providerRuntime.server_running !== "boolean"
          ? { server_running: true }
          : {}),
        ...(providerRuntime.loaded_model &&
        typeof providerRuntime.model_loaded !== "boolean"
          ? { model_loaded: true }
          : {}),
      }
    : null;
  const languageRuntimeContract = resolveRuntimePanelContract(
    {
      mode: settings.mode,
      apiStatus: svcApi,
      apiProviderStatus: state.apiProviderStatus,
      apiModel: settings.model || state.apiModel,
      apiEndpoint: settings.api_url,
      serverUrl: settings.server_url,
      serverModel: settings.transformer_model || state.transformerModel,
      serverRuntime,
      serverStatus:
        serverRuntime?.reachable === false
          ? "unavailable"
          : serverRuntime?.reachable === true
            ? "online"
            : "",
      serverLoading: serverRuntimeLoading,
      serverError: serverRuntimeError,
      localRuntimeKind: managedLocalRuntimeSelected ? "provider" : "direct",
      localModel: settings.transformer_model,
      providerMode: settings.local_provider_mode,
      localProviderBaseUrl: settings.local_provider_base_url,
      providerPreferredModel: settings.local_provider_preferred_model,
      providerRuntime: languageProviderRuntime,
      providerModels: providerModelOptions,
      providerLoading: providerRuntimeLoading || !!providerActionBusy,
      providerError: providerRuntimeDisplayError,
      runtime: localRuntime,
      localLoading: localRuntimeLoading || !!localRuntimeActionBusy,
      localError: localRuntimeError,
    },
    runtimeNow,
  );
  const languageRuntimeLaneLabel =
    LANGUAGE_RUNTIME_LANE_LABELS[languageRuntimeContract.lane] ||
    languageRuntimeContract.lane;
  const languageRuntimePanelClass =
    languageRuntimeContract.lane === RUNTIME_PANEL_LANES.CLOUD_API
      ? "runtime-inline-panel--api"
      : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER
        ? "runtime-inline-panel--provider"
        : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.DIRECT_LOCAL
          ? "runtime-inline-panel--local"
          : "";
  const languageRuntimeDescription =
    languageRuntimeContract.lane === RUNTIME_PANEL_LANES.CLOUD_API
      ? "Cloud API runtime for the selected provider model."
      : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.SERVER_LAN
        ? "OpenAI-compatible Server/LAN runtime for the configured endpoint."
        : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER
          ? "Provider-backed local runtime status for the selected bridge."
          : "Direct local Transformers runtime for the selected language model.";
  const languageRuntimeBusy =
    languageRuntimeContract.lane === RUNTIME_PANEL_LANES.SERVER_LAN
      ? serverRuntimeLoading
      : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER
        ? providerRuntimeLoading || !!providerActionBusy
        : languageRuntimeContract.lane === RUNTIME_PANEL_LANES.DIRECT_LOCAL
          ? localRuntimeLoading || !!localRuntimeActionBusy
          : svcApi === "loading";
  const providerRuntimeInspectorRows = buildProviderRuntimeInspectorRows({
    providerKey: selectedProviderKey,
    providerLabel: providerLabelText,
    providerRuntime,
    status: providerRuntimeStatus,
    summary: providerRuntimeSummary,
    detail: providerRuntimeDetail,
    ownershipWarning: providerOwnershipGuidance || providerCliWarning,
    lastOperation: providerRuntimeLastOperation,
    actionMessage: providerActionMessage,
  });
  const providerRuntimeFreshnessTone = providerRuntimeDisplayError
    ? "error"
    : providerCliWarning
      ? "warn"
    : providerExternalManagedServer || providerExternalManagedLoadedModel
      ? "warn"
    : providerRuntime?.loaded_model
      ? isChatCapableProviderModelName(providerRuntime.loaded_model)
        ? "ok"
        : "warn"
      : providerExternalEndpointMode
        ? providerEndpointReachable
          ? "ok"
          : "idle"
        : providerRuntime?.server_running || providerModelOptions.length > 0
          ? "warn"
        : "idle";
  const embeddingRuntime =
    ragStatus && typeof ragStatus.embedding_runtime === "object"
      ? ragStatus.embedding_runtime
      : null;

  const fieldTooltips = {

    transformer_model: "Language model for local inference or downloaded weights.",

    stt_model: "Speech-to-text model for transcribing audio.",

    tts_model: "Text-to-speech voice synthesis engine.",

    voice_model: "Voice preset for TTS (OpenAI, Kitten, Kokoro).",
    stream_backend:
      "Primary live streaming lane. API uses OpenAI Realtime; Local groups the Float bridge and legacy LiveKit transports.",
    realtime_model: "OpenAI Realtime model used for live streaming sessions.",
    realtime_voice: "Voice used by OpenAI Realtime during live streaming sessions.",
    live_agent_mode:
      "Target runtime lane for non-Realtime live orchestration such as LiveKit or a future Float local bridge.",
    live_agent_model:
      "Response model for non-Realtime live orchestration. Keep this separate from the transport model.",
    live_multimodal_model:
      "Optional visual-context model for live camera or screen input. Use this when the live response model is not itself multimodal.",

    vision_model: "Local caption and image-fallback model used when chat vision is not natively available.",

    rag_embedding_model: "Text embedding model used for semantic RAG search.",

  };

  const voiceOptionsForTts = useMemo(() => {
    const tts = String(settings.tts_model || "").toLowerCase();
    if (!tts) return voicePresetOptions;
    if (tts === "tts-1" || tts === "tts-1-hd") {
      return openAiLegacyTtsVoiceOptions;
    }
    if (tts.startsWith("gpt-4o") && tts.includes("tts")) {
      return openAiVoiceOptions;
    }
    if (tts.includes("kokoro")) return kokoroVoiceOptions;
    if (tts.includes("kitten")) return kittenVoiceOptions;
    return voicePresetOptions;
  }, [settings.tts_model]);

  const isKnownVoicePreset =
    !settings.voice_model ||
    voiceOptionsForTts.includes(settings.voice_model);

  const voicePresetInput = (
    <div className="model-inline-group">
      <div className="model-inline voice-inline model-inline--stacked">
        <label className="model-inline-label" htmlFor="settings-voice-model">
          TTS voice preset
          <SettingsInfoTip
            label="About TTS voice presets"
            text="OpenAI voice presets depend on the selected TTS model. Kitten and Kokoro use local presets, while live-streaming voice is configured separately."
          />
        </label>
        <input
          id="settings-voice-model"
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
          {voicePresetLooksLikeSpeechModel
            ? "This looks like a speech model id, not a TTS voice preset. Keep transcription and live voice models separate from the TTS preset."
            : "Voice preset doesn’t match the selected TTS model. Choose a preset or leave blank for provider defaults."}
        </div>
      )}
    </div>
  );



  // Local preference for Harmony defaulting while editing (before Save)

  const isHarmonyPreferred = (...models) => {

    try {

      return models

        .filter(Boolean)

        .map((model) => String(model).toLowerCase())

        .some((m) => isGptOssModel(m));

    } catch {

      return false;

    }

  };



  const preferHarmony = isHarmonyPreferred(

    settings.transformer_model,

    settings.model,

  );

  const harmonyMode = normalizeHarmonyFormatMode(settings.harmony_format_mode);
  const harmonyFormatEnabled = resolveHarmonyFormat(harmonyMode, preferHarmony);
  const harmonyWarning = preferHarmony && harmonyMode === "disabled";

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

  const handleHarmonyModeChange = (event) => {
    const mode = normalizeHarmonyFormatMode(event.target.value);
    setSettings((prev) => ({
      ...prev,
      harmony_format_mode: mode,
      harmony_format: resolveHarmonyFormat(mode, preferHarmony),
    }));
  };



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
          const repoId = String(modelInfos[field]?.repo_id || "").trim();
          if (repoId && !repoId.startsWith("TODO")) {
            window.open(`https://huggingface.co/${repoId}`, "_blank");
          } else if (isGemmaFamilyModel(model)) {
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
    const catalogModel = resolveLocalCatalogModelId(model);

    if (!model) return;

    try {

      setRegisterModelMessage("");
      setRegisterModelExplanation(null);
      setDownloadingModel((prev) => ({ ...prev, [field]: true }));

      await axios.delete(`/api/models/${encodeURIComponent(catalogModel)}`,

        useCustomModelsFolder && settings.models_folder

          ? { params: { path: settings.models_folder } }

          : undefined,

      );

      // Invalidate availability and sizes for this field

      setModelAvailable((prev) => ({ ...prev, [field]: false }));

      setModelLocalSizes((prev) => ({ ...prev, [field]: 0 }));
      setRegisterModelExplanation(null);
      setRegisterModelMessage(`Removed local model '${catalogModel}'.`);

      fetchAvailableModels(useCustomModelsFolder ? settings.models_folder : undefined);

    } catch (err) {

      const detail = err?.response?.data?.detail || err?.message;
      const message = extractStateExplanationMessage(detail, "Model deletion failed.");
      setRegisterModelExplanation({
        detail,
        model: catalogModel,
      });
      setRegisterModelMessage(message);
      alert(message);

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
    setRegisterModelExplanation(null);
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
      setRegisterModelExplanation(null);
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to register local model.";
      setRegisterModelMessage(String(detail));
      setRegisterModelExplanation(null);
    } finally {
      setRegisterModelBusy(false);
    }
  };

  const handleServerPresetChange = (event) => {
    const presetId = event.target.value;
    setSettings((prev) => {
      const presets = normalizeServerPresets(prev.server_presets);
      const preset = presets.find((item) => item.id === presetId);
      return {
        ...prev,
        server_preset_id: preset?.id || "",
        server_url: preset?.base_url || prev.server_url,
      };
    });
    setServerRuntime(null);
    setServerRuntimeError("");
  };

  const updateActiveServerPreset = (field, value) => {
    setSettings((prev) => {
      const presetId = prev.server_preset_id;
      const presets = normalizeServerPresets(prev.server_presets).map((preset) =>
        preset.id === presetId && !preset.builtin
          ? { ...preset, [field]: value }
          : preset,
      );
      return {
        ...prev,
        server_presets: presets,
        ...(field === "base_url" ? { server_url: value } : {}),
      };
    });
  };

  const handleServerUrlChange = (event) => {
    const value = event.target.value;
    setSettings((prev) => {
      const presets = normalizeServerPresets(prev.server_presets);
      const current = presets.find((preset) => preset.id === prev.server_preset_id);
      if (current && !current.builtin) {
        return {
          ...prev,
          server_url: value,
          server_presets: presets.map((preset) =>
            preset.id === current.id ? { ...preset, base_url: value } : preset,
          ),
        };
      }
      if (current && current.base_url !== value) {
        return { ...prev, server_url: value, server_preset_id: "" };
      }
      return { ...prev, server_url: value };
    });
  };

  const addCustomServerPreset = () => {
    const preset = makeCustomServerPreset(Date.now());
    setSettings((prev) => ({
      ...prev,
      server_preset_id: preset.id,
      server_url: "",
      server_presets: [...normalizeServerPresets(prev.server_presets), preset],
    }));
    setServerRuntime(null);
    setServerRuntimeError("");
  };

  const removeActiveServerPreset = () => {
    setSettings((prev) => {
      const presets = normalizeServerPresets(prev.server_presets);
      const current = presets.find((preset) => preset.id === prev.server_preset_id);
      if (!current || current.builtin) return prev;
      const remaining = presets.filter((preset) => preset.id !== current.id);
      const fallback = remaining.find((preset) => preset.id === "lm-studio-local");
      return {
        ...prev,
        server_presets: remaining,
        server_preset_id: fallback?.id || "",
        server_url: fallback?.base_url || "",
      };
    });
    setServerRuntime(null);
    setServerRuntimeError("");
  };

  const handleRegisterHfModel = async () => {
    const url = String(registerHfUrl || "").trim();
    if (!url) {
      setRegisterHfMessage("Hugging Face model URL or owner/repo is required.");
      return;
    }
    const payload = {
      url,
      model_type: registerHfType || "transformer",
      runtime: registerHfRuntime || "direct",
    };
    const alias = String(registerHfAlias || "").trim();
    if (alias) payload.alias = alias;
    setRegisterHfBusy(true);
    setRegisterHfMessage("");
    try {
      const response = await axios.post(
        "/api/models/registered/huggingface",
        payload,
      );
      const model = response?.data?.model || {};
      await fetchRegisteredLocalModels();
      setRegisterHfUrl("");
      setRegisterHfAlias("");
      setRegisterHfMessage(
        `Added '${model.alias || alias || model.repo_id}' from ${model.repo_id || url}.`,
      );
    } catch (err) {
      const detail =
        err?.response?.data?.detail || "Failed to add Hugging Face model.";
      setRegisterHfMessage(String(detail));
    } finally {
      setRegisterHfBusy(false);
    }
  };

  const handleUnregisterHfModel = async (alias) => {
    const modelAlias = String(alias || "").trim();
    if (!modelAlias) return;
    setRegisterHfBusy(true);
    setRegisterHfMessage("");
    try {
      await axios.delete(`/api/models/registered/${encodeURIComponent(modelAlias)}`);
      await fetchRegisteredLocalModels();
      setRegisterHfMessage(`Removed Hugging Face model '${modelAlias}'.`);
    } catch (err) {
      const detail =
        err?.response?.data?.detail || "Failed to remove Hugging Face model.";
      setRegisterHfMessage(String(detail));
    } finally {
      setRegisterHfBusy(false);
    }
  };

  const handleUnregisterLocalModel = async (alias) => {
    const modelAlias = String(alias || "").trim();
    if (!modelAlias) return;
    setRegisterModelBusy(true);
    setRegisterModelMessage("");
    setRegisterModelExplanation(null);
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
      setRegisterModelExplanation(null);
      setRegisterModelMessage(`Removed local model '${modelAlias}'.`);
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to remove local model.";
      setRegisterModelMessage(String(detail));
      setRegisterModelExplanation(null);
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

      harmony_format: resolveHarmonyFormat(
        normalizeHarmonyFormatMode(s.harmony_format_mode),
        isHarmonyPreferred(s.transformer_model, s.model),
      ),
      harmony_format_mode: normalizeHarmonyFormatMode(s.harmony_format_mode),

      server_url: s.server_url,
      server_preset_id: s.server_preset_id || "",
      server_presets: normalizeServerPresets(s.server_presets).filter(
        (preset) => !preset.builtin,
      ),

      stt_model: s.stt_model,

      tts_model: s.tts_model,

      voice_model: s.voice_model,
      stream_backend: s.stream_backend || "api",
      realtime_model: s.realtime_model || "",
      realtime_voice: s.realtime_voice || "",
      live_agent_mode: s.live_agent_mode || "local",
      live_agent_model: s.live_agent_model || "",
      live_multimodal_model: s.live_multimodal_model || "",
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
          : 0.45,
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
      background_autonomy_enabled: !!s.background_autonomy_enabled,
      background_autonomy_sandbox_processes:
        s.background_autonomy_sandbox_processes !== false,
      background_autonomy_mode: normalizeBackgroundAutonomyMode(
        s.background_autonomy_mode,
      ),
      background_autonomy_interval_seconds:
        typeof s.background_autonomy_interval_seconds === "number"
          ? s.background_autonomy_interval_seconds
          : 900,
      background_autonomy_max_reflections_per_tick:
        typeof s.background_autonomy_max_reflections_per_tick === "number"
          ? s.background_autonomy_max_reflections_per_tick
          : 1,
      background_autonomy_max_runtime_seconds:
        typeof s.background_autonomy_max_runtime_seconds === "number"
          ? s.background_autonomy_max_runtime_seconds
          : 1800,
      background_autonomy_satisfied_threshold:
        typeof s.background_autonomy_satisfied_threshold === "number"
          ? Math.min(1, Math.max(0, s.background_autonomy_satisfied_threshold))
          : 0.8,
      background_autonomy_basic_tick_count:
        typeof s.background_autonomy_basic_tick_count === "number"
          ? s.background_autonomy_basic_tick_count
          : 2,
      background_autonomy_basic_tick_seconds:
        typeof s.background_autonomy_basic_tick_seconds === "number"
          ? s.background_autonomy_basic_tick_seconds
          : 300,
      background_autonomy_min_priority:
        typeof s.background_autonomy_min_priority === "number"
          ? Math.min(1, Math.max(0, s.background_autonomy_min_priority))
          : 0.05,
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
    const isLanguageField = field === "transformer_model";
    const languageLaneKey = isLanguageField
      ? settings.mode === "api"
        ? "api"
        : "local"
      : null;
    const model = isLanguageField
      ? languageLaneKey === "api"
        ? settings.model || suggestedApiLangModels[0] || DEFAULT_API_MODELS[0] || ""
        : settings[field] || ""
      : settings[field] || "";

    const downloadBlocked =
      (isLanguageField && languageLaneKey === "api") || NON_DOWNLOADABLE.has(model);

    const available = isLanguageField && languageLaneKey === "api" ? true : modelAvailable[field];

    const info =
      isLanguageField && languageLaneKey === "api"
        ? { size: 0, repo_id: null }
        : modelInfos[field] || { size: 0, repo_id: null };

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

    const modelIsApiOnly =
      (isLanguageField && languageLaneKey === "api") || API_ONLY.has(model);

    const downloadable =
      !modelIsApiOnly &&
      !(isLanguageField && languageLaneKey === "api") &&
      (modelDownloadable[field] ?? true);
    const laneMeta = isLanguageField
      ? { key: languageLaneKey, label: getLaneDisplayLabel(languageLaneKey) }
      : getModelLaneMeta(field, model, info);
    const laneClass = laneMeta ? ` model-lane-${laneMeta.key}` : "";

    const optionMeta = (m) => {
      const value = typeof m === "string" ? m.trim() : "";
      const isApiOnly = Boolean(
        value &&
          (API_ONLY.has(value) ||
            (isLanguageField && suggestedApiLangModels.includes(value))),
      );
      const isSuggested = Boolean(value && suggestions.includes(value));
      const isProviderInventory = Boolean(
        isLanguageField && value && providerModelOptionsSet.has(value),
      );
      const isAvailable = Boolean(
        value && (availableModelSet.has(value) || isProviderInventory),
      );
      const isRegistered = Boolean(value && registeredModelAliasSet.has(value));
      const optionLaneKey =
        isLanguageField && suggestedApiLangModels.includes(value)
          ? "api"
          : isProviderInventory
            ? "provider"
          : getModelLaneMeta(field, value)?.key || laneMeta?.key || "local";
      const className = `model-option ${
        isApiOnly
          ? "model-option-api"
          : isProviderInventory
            ? "model-option-provider-available"
          : isAvailable
            ? "model-option-available"
            : isSuggested
              ? "model-option-suggested"
              : "model-option-unknown"
      }`;
      const apiLabel = formatApiModelLabel(value, {
        aliases: apiModelAliases,
        availableModels: apiModelsAvailable,
        catalog: apiModelCatalog,
      });
      const labelText = isApiOnly
        ? `${apiLabel || value} (API)`
        : isProviderInventory
          ? `✓ ${value}`
        : isAvailable
          ? `✓ ${value}`
        : isSuggested
          ? `☆ ${value}`
          : value;
      const providerLabel = isProviderInventory
        ? "available on provider"
        : describeModelProvider(field, value);
      return {
        value,
        className,
        laneKey: optionLaneKey,
        isApiOnly,
        isAvailable,
        isProviderInventory,
        isRegistered,
        isSuggested,
        labelText: [
          isRegistered ? `${labelText} (local)` : labelText,
          providerLabel ? `\u00b7 ${providerLabel}` : "",
        ]
          .filter(Boolean)
          .join(" "),
      };
    };

    const registeredOptions = registeredModelOptionsByField[field] || [];
    const options = isLanguageField
      ? [
          ...suggestions,
          ...registeredOptions.filter((m) => !suggestions.includes(m)),
          ...filterAvailableModelsForField(field, availableModels, {
            includeAll: includeCacheUnfiltered,
          }).filter((m) => !suggestions.includes(m) && !registeredOptions.includes(m)),
          ...suggestedApiLangModels,
        ]
      : [
          ...suggestions,
          ...registeredOptions.filter((m) => !suggestions.includes(m)),
          ...filterAvailableModelsForField(field, availableModels, {
            includeAll: includeCacheUnfiltered,
          }).filter((m) => !suggestions.includes(m) && !registeredOptions.includes(m)),
        ];

    const optionEntries = options.map((entry) => optionMeta(entry));
    if (!optionEntries.some((entry) => entry.value === model) && model) {
      optionEntries.push(optionMeta(model));
    }
    const laneOptions = isLanguageField
      ? ["api", "local"]
      : Array.from(new Set(optionEntries.map((entry) => entry.laneKey).filter(Boolean)));
    const activeLaneKey = isLanguageField
      ? languageLaneKey
      : laneMeta?.key || laneOptions[0] || null;
    const laneVisibleOptionEntries =
      laneOptions.length > 1
        ? optionEntries.filter((entry) => entry.laneKey === activeLaneKey)
        : optionEntries;
    const downloadedOnlyActive =
      showDownloadedOnly && !(isLanguageField && activeLaneKey === "api");
    const installedOnlyEntries = downloadedOnlyActive
      ? laneVisibleOptionEntries.filter(
          (entry) =>
            entry.isAvailable || entry.isProviderInventory || entry.isRegistered,
        )
      : laneVisibleOptionEntries;
    const visibleOptionEntries = installedOnlyEntries;
    const currentSelectionVisible =
      !model || visibleOptionEntries.some((entry) => entry.value === model);
    const currentSelectionFilteredOut =
      downloadedOnlyActive && !!model && !currentSelectionVisible;
    const selectValue = currentSelectionVisible ? model : "";

    return (

      <div className="settings-model-block">
        <div className="settings-model-heading">
          <label
            className="settings-model-heading-main"
            htmlFor={`settings-model-${field}`}
            title={fieldTooltips[field] || label}
          >
            {label}
          </label>
          <div className="settings-model-heading-meta">
            {renderLaneSelector(field, activeLaneKey, laneOptions, (nextLaneKey) => {
              if (isLanguageField) {
                if (nextLaneKey === "api") {
                  setSettings((prev) => ({
                    ...prev,
                    mode: "api",
                    model:
                      prev.model ||
                      suggestedApiLangModels[0] ||
                      DEFAULT_API_MODELS[0] ||
                      "",
                  }));
                  return;
                }
                setSettings((prev) => ({
                  ...prev,
                  mode: "local",
                  transformer_model: prev.transformer_model || suggestions[0] || "",
                }));
                return;
              }
              const nextEntry =
                optionEntries.find(
                  (entry) =>
                    entry.laneKey === nextLaneKey &&
                    (entry.isAvailable ||
                      entry.isProviderInventory ||
                      entry.isRegistered),
                ) || optionEntries.find((entry) => entry.laneKey === nextLaneKey);
              if (nextEntry) {
                commitSettingValue(field, nextEntry.value);
              }
            })}
            {laneMeta && laneOptions.length <= 1 && (
              <span className={`model-lane-pill model-lane-pill--${laneMeta.key}`}>
                {laneMeta.label}
              </span>
            )}
          </div>
        </div>

        <div className="model-select-stack">
          <div
            className={`model-select-row ${
              available ? "model-present" : "model-missing"
            }${laneClass}`}
          >
            <select
              id={`settings-model-${field}`}
              name={isLanguageField && activeLaneKey === "api" ? "model" : field}
              value={selectValue}
              onChange={
                isLanguageField
                  ? (event) => {
                      if (!event.target.value) return;
                      commitSettingValue(
                        activeLaneKey === "api" ? "model" : field,
                        event.target.value,
                      );
                    }
                  : (event) => {
                      if (!event.target.value) return;
                      handleChange(event);
                    }
              }
              title={fieldTooltips[field] || `Select ${label}`}
            >
              {visibleOptionEntries.length === 0 && (
                <option value="" disabled>
                  No downloaded models in this lane
                </option>
              )}
              {currentSelectionFilteredOut && (
                <option value="" disabled>
                  Choose a downloaded model
                </option>
              )}
              {visibleOptionEntries.map((meta) => {
                return (
                  <option key={meta.value} value={meta.value} className={meta.className}>
                    {meta.labelText}
                  </option>
                );
              })}
            </select>

            <div className={`model-action-group model-action-group--${activeLaneKey || "local"}`}>
              <button
                type="button"
                className="icon-btn"
                title={
                  currentSelectionFilteredOut
                    ? "Choose a downloaded model before using model actions"
                    : downloadBlocked
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
                  currentSelectionFilteredOut ||
                  downloadBlocked ||
                  !downloadable ||
                  (available && verified)
                }
              >
                ⬇️
              </button>

              {repoId && !String(repoId).startsWith("TODO") && !currentSelectionFilteredOut && (
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
                title={
                  currentSelectionFilteredOut
                    ? "Choose a downloaded model before opening its folder"
                    : available
                      ? "Open containing folder"
                      : "Model not present"
                }
                onClick={async () => {
                  try {
                    await axios.get(
                      `/api/models/reveal/${encodeURIComponent(resolveLocalCatalogModelId(model))}`,
                      useCustomModelsFolder && settings.models_folder
                        ? { params: { path: settings.models_folder } }
                        : undefined,
                    );
                  } catch (e) {
                    alert("Unable to open folder on host.");
                  }
                }}
                disabled={!available || currentSelectionFilteredOut}
              >
                📂
              </button>

              <button
                type="button"
                className="icon-btn"
                title={
                  currentSelectionFilteredOut
                    ? "Choose a downloaded model before deleting"
                    : "Delete model"
                }
                onClick={() => handleModelDelete(field)}
                disabled={
                  !!downloadingModel[field] ||
                  currentSelectionFilteredOut ||
                  !available ||
                  downloadBlocked
                }
              >
                🗑️
              </button>
            </div>

            <div className="model-row-trailing">
              {renderCapabilityStrip(field, model, info)}
              <span
                className="model-size"
                title={
                  verified
                    ? "expected / installed size (checksum verified)"
                    : "expected size / installed (checksum pending)"
                }
              >
                {expectedSizeGB} / {installedSizeGB}
                {verified ? " ✓" : ""}
              </span>
            </div>
          </div>

          {currentSelectionFilteredOut && (
            <div className="status-note warn form-note">
              Saved selection <code>{model}</code> is not downloaded or registered in this lane.
            </div>
          )}

          {extra && <div className="model-inline-panel">{extra}</div>}
        </div>
      </div>

    );

  };



  const handleSave = () => {

    const incompleteServerPreset = normalizeServerPresets(
      settings.server_presets,
    ).find(
      (preset) =>
        !preset.builtin && (!preset.name.trim() || !preset.base_url.trim()),
    );
    if (incompleteServerPreset) {
      setMessage("Give each custom server preset a name and base URL before saving.");
      return;
    }

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

      harmony_format: harmonyFormatEnabled,
      harmony_format_mode: normalizeHarmonyFormatMode(settings.harmony_format_mode),

      server_url: settings.server_url,
      server_preset_id: settings.server_preset_id || "",
      server_presets: normalizeServerPresets(settings.server_presets).filter(
        (preset) => !preset.builtin,
      ),

      stt_model: settings.stt_model,

      tts_model: settings.tts_model,

      voice_model: settings.voice_model,
      stream_backend: settings.stream_backend || "api",
      realtime_model: settings.realtime_model,
      realtime_voice: settings.realtime_voice,
      live_agent_mode: settings.live_agent_mode,
      live_agent_model: settings.live_agent_model,
      live_multimodal_model: settings.live_multimodal_model,
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
      background_autonomy_enabled: !!settings.background_autonomy_enabled,
      background_autonomy_sandbox_processes:
        settings.background_autonomy_sandbox_processes !== false,
      background_autonomy_mode: normalizeBackgroundAutonomyMode(
        settings.background_autonomy_mode,
      ),
      background_autonomy_interval_seconds:
        settings.background_autonomy_interval_seconds,
      background_autonomy_max_reflections_per_tick:
        settings.background_autonomy_max_reflections_per_tick,
      background_autonomy_max_runtime_seconds:
        settings.background_autonomy_max_runtime_seconds,
      background_autonomy_satisfied_threshold:
        settings.background_autonomy_satisfied_threshold,
      background_autonomy_basic_tick_count:
        settings.background_autonomy_basic_tick_count,
      background_autonomy_basic_tick_seconds:
        settings.background_autonomy_basic_tick_seconds,
      background_autonomy_min_priority: settings.background_autonomy_min_priority,
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
        const savedHarmonyMode = normalizeHarmonyFormatMode(
          settings.harmony_format_mode,
        );
        let nextSettings = {
          ...settings,
          harmony_format_mode: savedHarmonyMode,
          harmony_format: resolveHarmonyFormat(savedHarmonyMode, preferHarmony),
        };
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
          !nextSettings.live_agent_mode ||
          !nextSettings.realtime_base_url ||
          !nextSettings.realtime_connect_url;
        if (normalizedRealtimeDefaults) {
          nextSettings = {
            ...nextSettings,
            stream_backend: nextSettings.stream_backend || "api",
            realtime_model: nextSettings.realtime_model || "gpt-realtime-2.1",
            realtime_voice: nextSettings.realtime_voice || "alloy",
            live_agent_mode: nextSettings.live_agent_mode || "local",
            live_agent_model: nextSettings.live_agent_model || "",
            live_multimodal_model: nextSettings.live_multimodal_model || "",
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

        refreshStatus();

      })

      .catch(() => {

        setMessage("Error saving settings.");

      })

      .finally(() => {

        setSaving(false);
 
        setState((prev) => ({
 
          ...prev,
 
          backendMode: settings.mode,
          runtimeSelectionTouchedAt: Date.now(),

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
          ...buildGlobalSelectionPatch(prev, settings),
          ...(settings.mode === "server"
            ? { transformerModel: settings.transformer_model || "" }
            : {}),
 
          staticModel: settings.static_model,

          approvalLevel: settings.approvalLevel,

          harmonyFormatMode: normalizeHarmonyFormatMode(settings.harmony_format_mode),
          harmonyFormat: resolveHarmonyFormat(
            normalizeHarmonyFormatMode(settings.harmony_format_mode),
            preferHarmony,
          ),

          serverUrl: settings.server_url,

          sttModel: settings.stt_model,

          ttsModel: settings.tts_model,

          voiceModel: settings.voice_model,

          visionModel: settings.vision_model,
          ragEmbeddingModel: settings.rag_embedding_model,
          ragClipModel: settings.rag_clip_model,

          maxContextLength: settings.context_length,

          kvCache: settings.kv_cache,

          ramSwap: settings.ram_swap,

        }));

      });

  };

  const handleBackgroundAutonomyDryRun = async () => {
    setBackgroundAutonomyTickBusy(true);
    setBackgroundAutonomyMessage("");
    try {
      const mode = normalizeBackgroundAutonomyMode(settings.background_autonomy_mode);
      const response = await axios.post("/api/background/autonomy/tick", {
        mode,
        dry_run: true,
        max_reflections:
          mode === "basic"
            ? settings.background_autonomy_basic_tick_count
            : settings.background_autonomy_max_reflections_per_tick,
        max_runtime_seconds: settings.background_autonomy_max_runtime_seconds,
        satisfied_threshold: settings.background_autonomy_satisfied_threshold,
      });
      const tick = response?.data?.tick || {};
      const autonomy = response?.data?.autonomy || null;
      if (autonomy && typeof autonomy === "object") {
        setBackgroundAutonomyStatus(autonomy);
      }
      const candidates = Number(tick.candidate_count || 0);
      setBackgroundAutonomyMessage(
        `Dry run ${tick.status || "planned"}: ${candidates} candidate${
          candidates === 1 ? "" : "s"
        } visible.`,
      );
    } catch {
      setBackgroundAutonomyMessage("Dry run failed.");
    } finally {
      setBackgroundAutonomyTickBusy(false);
    }
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

  const handleCapturePrivacySave = async () => {
    const nextRetentionDays = Math.max(1, Number(captureRetentionDays) || 7);
    const nextPrivacyFilterMode = normalizePrivacyFilterMode(privacyFilterMode);
    const nextPrivacyFilterModel =
      String(privacyFilterModel || "").trim() || "openai/privacy-filter";
    const nextPrivacyRouteMode = normalizePrivacyRouteMode(privacyRouteMode);
    setCapturePrivacySaving(true);
    setCapturePrivacyMessage("");
    try {
      await axios.post("/api/user-settings", {
        capture_retention_days: nextRetentionDays,
        capture_default_sensitivity: captureDefaultSensitivity || "personal",
        capture_allow_model_raw_image_access: captureAllowModelRawImageAccess !== false,
        capture_allow_summary_fallback: captureAllowSummaryFallback !== false,
        privacy_filter_mode: nextPrivacyFilterMode,
        privacy_filter_model: nextPrivacyFilterModel,
        privacy_filter_route_private_mode: nextPrivacyRouteMode,
      });
      setPrivacyFilterMode(nextPrivacyFilterMode);
      setPrivacyFilterModel(nextPrivacyFilterModel);
      setPrivacyRouteMode(nextPrivacyRouteMode);
      setState((prev) => ({
        ...prev,
        captureRetentionDays: nextRetentionDays,
        captureDefaultSensitivity: captureDefaultSensitivity || "personal",
        captureAllowModelRawImageAccess: captureAllowModelRawImageAccess !== false,
        captureAllowSummaryFallback: captureAllowSummaryFallback !== false,
      }));
      setCapturePrivacyMessage("Capture and privacy settings saved.");
    } catch {
      setCapturePrivacyMessage("Failed to save capture and privacy settings.");
    } finally {
      setCapturePrivacySaving(false);
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

  return (

    <div className="settings-container" ref={settingsContainerRef}>

      {loading ? (

        loadingView

      ) : (

        <div className="settings-shell settings-section">
          <section className="settings-toolbar-card settings-topbar" ref={settingsToolbarRef}>
            <div className="settings-topbar-title">
              <h1>settings</h1>
            </div>
            <div className="settings-topbar-search">
              <label className="settings-topbar-search-label" htmlFor="settings-page-search">
                Search settings
              </label>
              <input
                id="settings-page-search"
                type="search"
                value={settingsSearch}
                onChange={(e) => setSettingsSearch(e.target.value)}
                placeholder="search"
                aria-label="Search settings"
              />
              {settingsSearch && (
                <button
                  type="button"
                  className="icon-btn settings-search-clear"
                  onClick={() => setSettingsSearch("")}
                  style={{ marginTop: 0 }}
                  aria-label="Clear search"
                >
                  Clear
                </button>
              )}
            </div>
            <div className="settings-topbar-nav" role="navigation" aria-label="Settings sections">
              {SETTINGS_SECTIONS.map((section) => {
                const isVisible = showSettingsSection(section.id);
                const isActive = activeSettingsSection === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    className={`settings-topbar-button${
                      isActive ? " is-active" : ""
                    }${isVisible ? "" : " is-filtered"}`}
                    aria-current={isActive ? "true" : undefined}
                    aria-label={`${section.label}. ${section.description}`}
                    title={section.description}
                    onClick={() => handleSettingsNavClick(section.id)}
                  >
                    {section.label}
                  </button>
                );
              })}
            </div>
            <div className="settings-topbar-actions">
              <button onClick={handleSave} disabled={saving || !isDirty}>
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </section>
          <p className="status-note settings-toolbar-note" aria-live="polite">
            {settingsSearchTerms.length
              ? `Showing ${visibleSettingsSections.length} of ${SETTINGS_SECTIONS.length} sections for "${settingsSearch.trim()}".`
              : "Use the pinned bar to search, jump between sections, and save without leaving the current scroll position."}
          </p>
          <div className="settings-content settings-content--full">
            {/* Consolidated runtime status indicators */}
            {renderStatusSection()}

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

          <div className="settings-link-row">
            <div>
              <strong>Device sync</strong>
              <span>Pair devices, review changes, and manage trust in Knowledge Sync.</span>
            </div>
            <Link to="/knowledge?tab=sync" className="icon-btn settings-inline-link">
              Open Knowledge Sync
            </Link>
          </div>

          <details className="settings-disclosure settings-disclosure--advanced">
            <summary>
              <span>Optional Weaviate connection</span>
              <SettingsInfoTip
                label="About Weaviate"
                text="Float uses SQLite for canonical knowledge and Chroma for retrieval by default. Configure Weaviate only for a deliberate external backend or import workflow."
              />
            </summary>
            <div className="settings-disclosure-body">
              <label title="Weaviate base URL (http/https)">Weaviate URL</label>
              <input
                name="weaviate_url"
                type="text"
                value={settings.weaviate_url}
                onChange={handleChange}
                placeholder="http://localhost:8080"
                title="Weaviate base URL (http/https)"
              />
              <div className="settings-compact-actions">
                <button
                  type="button"
                  className="runtime-inline-btn"
                  onClick={refreshWeaviateStatus}
                  disabled={wvLoading}
                >
                  {wvLoading ? "Checking…" : "Check status"}
                </button>
                {weaviateStatus.reachable == null ? (
                  <span className="status-badge status-badge--loading">checking</span>
                ) : weaviateStatus.reachable ? (
                  <span className="status-badge status-badge--online">reachable</span>
                ) : (
                  <span className="status-badge status-badge--offline">unreachable</span>
                )}
                <button
                  type="button"
                  className="runtime-inline-btn"
                  onClick={handleWeaviateStart}
                  disabled={wvStarting}
                >
                  {wvStarting ? "Starting…" : "Start Weaviate"}
                </button>
              </div>
              {wvMessage && (
                <div className="settings-message" role="status">
                  {wvMessage}
                </div>
              )}
              <label className="settings-toggle-row">
                <input
                  name="weaviate_auto_start"
                  type="checkbox"
                  checked={!!settings.weaviate_auto_start}
                  onChange={(event) =>
                    setSettings((prev) => ({
                      ...prev,
                      weaviate_auto_start: !!event.target.checked,
                    }))
                  }
                />
                <span>Auto-start Weaviate through Docker when needed</span>
              </label>
            </div>
          </details>

            </section>
          )}

          {showSettingsSection("connections") && (
            <section
              id="settings-runtime"
              className="settings-card settings-section"
              aria-label="Language runtime connections"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Language Runtime Connections</h2>
                  <p className="settings-card-copy">
                    Choose cloud API, on-device Transformers, or a compatible
                    server connection.
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
          <div className="status-note form-note">
            Local means direct Transformers or a configured provider bridge.
          </div>

        {settings.mode === "server" && (
          <>
            <label title="Choose a built-in server connection or a saved custom endpoint.">
              Server connection preset
            </label>
            <div className="server-preset-controls">
              <select
                aria-label="Server connection preset"
                value={settings.server_preset_id || ""}
                onChange={handleServerPresetChange}
              >
                <option value="">Manual URL</option>
                {normalizeServerPresets(settings.server_presets).map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </select>
              <button type="button" className="runtime-inline-btn" onClick={addCustomServerPreset}>
                New custom
              </button>
              {!activeServerPreset?.builtin && activeServerPreset && (
                <button
                  type="button"
                  className="runtime-inline-btn"
                  onClick={removeActiveServerPreset}
                >
                  Remove
                </button>
              )}
            </div>
            {activeServerPreset?.description && (
              <div className="status-note form-note">{activeServerPreset.description}</div>
            )}

            {activeServerPreset && !activeServerPreset.builtin && (
              <>
                <label title="Friendly name shown in the preset list.">Preset name</label>
                <input
                  type="text"
                  aria-label="Preset name"
                  value={activeServerPreset.name}
                  onChange={(event) =>
                    updateActiveServerPreset("name", event.target.value)
                  }
                  placeholder="My inference endpoint"
                />
                <label title="Provider hint used for account-aware inventory and warnings.">
                  Provider type
                </label>
                <input
                  type="text"
                  aria-label="Provider type"
                  value={activeServerPreset.provider}
                  onChange={(event) =>
                    updateActiveServerPreset("provider", event.target.value)
                  }
                  placeholder="openai-compatible"
                />
              </>
            )}

            <label title="URL for an OpenAI-compatible LLM server or hosted provider.">
              Server/LAN URL
            </label>
            <input
              name="server_url"
              type="text"
              value={settings.server_url}
              onChange={handleServerUrlChange}
              placeholder="http://127.0.0.1:1234/v1"
              title="URL for an OpenAI-compatible LLM server or hosted provider."
            />
            <SettingsInfoTip
              label="Server connection details"
              text="Provider keys are read server-side from environment variables."
            />

            {activeServerPreset && !activeServerPreset.builtin && (
              <>
                <label title="Environment variable containing this provider's bearer token.">
                  API key environment variable
                </label>
                <input
                  type="text"
                  aria-label="API key environment variable"
                  value={activeServerPreset.api_key_env}
                  onChange={(event) =>
                    updateActiveServerPreset("api_key_env", event.target.value.toUpperCase())
                  }
                  placeholder="MY_PROVIDER_API_KEY"
                  autoCapitalize="characters"
                />
              </>
            )}

            {activeServerPreset?.api_key_env && (
              <div
                className={`status-note form-note${
                  activeServerPreset.api_key_set ? "" : " warn"
                }`}
              >
                {activeServerPreset.api_key_set
                  ? `${activeServerPreset.api_key_env} detected (hidden).`
                  : `${activeServerPreset.api_key_env} is not set in the Float process environment.`}
              </div>
            )}

            {activeServerTrustWarning && (
              <div className="status-note warn form-note" role="alert">
                {activeServerTrustWarning}
              </div>
            )}

            <label title="Model id sent to the Server/LAN endpoint. Leave blank only when the server should choose its loaded model.">
              Server model
            </label>
            <input
              name="transformer_model"
              type="text"
              value={settings.transformer_model || ""}
              onChange={handleChange}
              placeholder={providerModelOptions[0] || "gemma-4-12B-it-qat-q4_0-gguf"}
              list="server-runtime-model-options"
              title="Model id sent to the Server/LAN endpoint."
            />
            <datalist id="server-runtime-model-options">
              {serverRuntimeModelOptions.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </>
        )}

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

                <optgroup label={apiModelGroups.source === "discovered" ? "available" : "defaults"}>
                  {apiModelGroups.defaults.map((m) => {
                    const disabled =
                      apiModelsAvailableSet.size > 0 &&
                      !apiModelsAvailableSet.has(m);
                    const displayLabel = formatApiModelLabel(m, {
                      aliases: apiModelAliases,
                      availableModels: apiModelsAvailable,
                      catalog: apiModelCatalog,
                    });
                    const label = disabled
                      ? `${displayLabel || m} (unavailable)`
                      : displayLabel || m;
                    return (
                      <option key={m} value={m} disabled={disabled}>
                        {label}
                      </option>
                    );
                  })}
                </optgroup>
                {apiModelGroups.extras.length > 0 && (
                  <optgroup
                    label="current selection"
                  >
                    {apiModelGroups.extras.map((m) => (
                      <option key={m} value={m}>
                        {formatApiModelLabel(m, {
                          aliases: apiModelAliases,
                          availableModels: apiModelsAvailable,
                          catalog: apiModelCatalog,
                        }) || m}
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

                {suggestedLangModels.map((m) => {
                  const modelAvailableHere =
                    availableModelSet.has(m) || providerModelOptionsSet.has(m);
                  const label = isLocalRuntimeEntry(m) ? formatLocalRuntimeLabel(m) : m;
                  return (
                    <option
                      key={m}
                      value={m}
                      className={
                        providerModelOptionsSet.has(m)
                          ? "model-option model-option-provider-available"
                          : modelAvailableHere
                            ? "model-option model-option-available"
                            : "model-option model-option-suggested"
                      }
                    >
                      {label}
                    </option>
                  );
                })}

                {settings.transformer_model &&
                  !suggestedLangModels.includes(settings.transformer_model) && (
                    <option
                      value={settings.transformer_model}
                      className={
                        availableModelSet.has(settings.transformer_model)
                          ? "model-option model-option-available"
                          : "model-option model-option-unknown"
                      }
                    >
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
                <summary>
                  External provider compatibility (LM Studio / Ollama / OpenAI-compatible)
                </summary>

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

                <label title="Optional explicit base URL for provider HTTP API. Hugging Face router URLs can reuse the stored HF token; OpenAI URLs can reuse the stored OpenAI key.">
                  Provider Base URL
                </label>
                <input
                  name="local_provider_base_url"
                  value={settings.local_provider_base_url || ""}
                  onChange={handleChange}
                  placeholder="http://127.0.0.1:1234/v1 or https://router.huggingface.co/v1"
                  title="Set an OpenAI-compatible endpoint. If Provider API Token is blank, Hugging Face URLs use the stored HF token and OpenAI URLs use the stored OpenAI key."
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
                <div className="status-note form-note">
                  Explicit provider token wins. If blank, Hugging Face router URLs
                  use the stored HF token and OpenAI API URLs use the stored
                  OpenAI key; local LM Studio/Ollama endpoints stay unauthenticated
                  unless you set this field.
                </div>
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
                    <div className="inline-flex" style={{ gap: 8, alignItems: "center" }}>
                      {renderStatusBadge(providerRuntimeStatus)}
                      <StateInspector
                        title="Why this provider runtime is shown"
                        summary="Provider status combines runtime inventory, ownership checks, and the last bridge action."
                        rows={providerRuntimeInspectorRows}
                        ariaLabel="Explain provider runtime state"
                      />
                    </div>
                  </div>
                  <p className="status-note" style={{ marginTop: 6 }}>
                    {providerRuntimeSummary}
                  </p>
                  <p className={`status-note${providerRuntimeError ? " warn" : ""}`}>
                    {providerRuntimeDetail}
                  </p>
                  {providerRuntimeLastOperation ? (
                    <p
                      className={`status-note${
                        providerRuntimeLastOperation.status === "failed" ? " warn" : ""
                      }`}
                      title={providerRuntimeLastOperation.title}
                    >
                      {providerRuntimeLastOperation.label}
                    </p>
                  ) : null}
                  {providerRuntime?.context_length ? (
                    <p className="status-note">
                      Active context length: {providerRuntime.context_length}
                    </p>
                  ) : null}
                  {providerCliWarning ? (
                    <div className="runtime-inline-message runtime-inline-message--error">
                      {providerCliWarning}
                    </div>
                  ) : null}
                  <div className="inline-flex" style={{ gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => refreshProviderRuntime(false, { refresh: true })}
                      disabled={providerRuntimeLoading || !providerRuntimeInspectable}
                      style={{ marginTop: 0 }}
                    >
                      {providerRuntimeLoading ? "Refreshing..." : "Refresh"}
                    </button>
                    <span
                      className={`runtime-freshness-indicator ${providerRuntimeFreshnessTone}`}
                      title={providerRuntimeFreshnessTooltip}
                      aria-label="Provider inventory freshness"
                    />
                    {!providerExternalEndpointMode && (
                      <>
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
                      </>
                    )}
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
                  {providerRuntimeInspectable && providerPreferredModelOptions.length > 0 && (
                    <select
                      className="provider-model-picker"
                      value={
                        providerModelOptionsSet.has(settings.local_provider_preferred_model)
                          ? settings.local_provider_preferred_model
                          : ""
                      }
                      onChange={(event) => {
                        const nextModel = event.target.value;
                        if (nextModel) {
                          commitSettingValue("local_provider_preferred_model", nextModel);
                        }
                      }}
                      title="Models reported by the selected provider runtime."
                      aria-label="Provider reported models"
                    >
                      <option value="">Reported models...</option>
                      {providerPreferredModelOptions.map((model) => (
                        <option
                          key={model}
                          value={model}
                          className={
                            providerModelOptionsSet.has(model)
                              ? "model-option model-option-provider-available"
                              : "model-option model-option-unknown"
                          }
                        >
                          {providerModelOptionsSet.has(model)
                            ? `● ${model}`
                            : model}
                        </option>
                      ))}
                    </select>
                  )}
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

          <div className="model-library-header">
            <div className="settings-heading-with-help">
              <h3 className="settings-subsection-title">Model library</h3>
              <SettingsInfoTip
                label="About model downloads"
                text="Downloads use the configured models folder, normally data/models. Direct Transformers expects Hugging Face checkpoint folders; GGUF models run through LM Studio, Ollama, or another provider."
              />
            </div>
            <button type="button" className="icon-btn" onClick={openDownloadsTray}>
              Downloads
            </button>
          </div>
          <div className="model-library-controls">
            <label
              className="field-label model-library-toggle"
              title="Only show installed, provider-discovered, or registered models in dropdowns. The currently selected model remains visible."
            >
              <input
                type="checkbox"
                checked={showDownloadedOnly}
                onChange={(e) => setShowDownloadedOnly(!!e.target.checked)}
              />
              <span>Downloaded only</span>
            </label>
            <label
              className="field-label model-library-toggle"
              title="Include every Hugging Face cache entry, even tiny utility models that are usually hidden from chat selectors."
            >
              <input
                type="checkbox"
                checked={includeCacheUnfiltered}
                onChange={(e) => {
                  const next = e.target.checked;
                  setIncludeCacheUnfiltered(next);
                }}
              />
              <span>Include noisy HF cache entries</span>
            </label>
          </div>
          <div className="settings-collapsible-heading">
            <div className="settings-heading-with-help">
              <h4>Add Hugging Face model</h4>
              <SettingsInfoTip
                label="About Hugging Face registration"
                text="Accepts a huggingface.co model URL or owner/repo. Registration saves catalog metadata; use Downloads separately to fetch weights."
              />
            </div>
            <button
              type="button"
              className="runtime-inline-btn"
              aria-expanded={hfRegisterOpen}
              onClick={() => setHfRegisterOpen((open) => !open)}
            >
              {hfRegisterOpen ? "Close form" : "Add model"}
            </button>
          </div>
          {hfRegisterOpen && (
            <div className="settings-collapsible-body">
              <div className="model-register-row">
                <input
                  type="text"
                  value={registerHfAlias}
                  onChange={(event) => setRegisterHfAlias(event.target.value)}
                  placeholder="Alias (optional)"
                  title="Name shown in Float model selectors. Defaults to the Hugging Face repository name."
                />
                <input
                  type="url"
                  value={registerHfUrl}
                  onChange={(event) => setRegisterHfUrl(event.target.value)}
                  placeholder="https://huggingface.co/owner/model or owner/model"
                  title="Hugging Face model page URL or owner/repo identifier."
                />
                <select
                  value={registerHfType}
                  onChange={(event) => setRegisterHfType(event.target.value)}
                  title="Model type for type-aware dropdown placement."
                >
                  <option value="transformer">Language</option>
                  <option value="stt">Speech-to-text</option>
                  <option value="tts">Text-to-speech</option>
                  <option value="vision">Vision</option>
                  <option value="voice">Voice</option>
                  <option value="other">Other</option>
                </select>
                <select
                  value={registerHfRuntime}
                  onChange={(event) => setRegisterHfRuntime(event.target.value)}
                  title="Direct Transformers loads a checkpoint in Float. Provider/GGUF downloads weights for LM Studio, Ollama, or another compatible runtime."
                >
                  <option value="direct">Direct Transformers</option>
                  <option value="provider">Provider / GGUF</option>
                </select>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={handleRegisterHfModel}
                  disabled={registerHfBusy}
                  title="Add Hugging Face model to personal catalog"
                >
                  + Add
                </button>
              </div>
              {registerHfMessage && (
                <div className="settings-message model-register-message" role="status">
                  {registerHfMessage}
                </div>
              )}
            </div>
          )}
          {registeredLocalModels.some(
            (entry) => entry?.source_type === "huggingface",
          ) && (
            <div className="model-register-list">
              {registeredLocalModels
                .filter((entry) => entry?.source_type === "huggingface")
                .map((entry) => {
                  const alias = String(entry?.alias || "").trim();
                  const repoId = String(entry?.repo_id || "").trim();
                  const modelType = String(entry?.model_type || "other").trim();
                  const runtime = String(entry?.runtime || "direct").trim();
                  if (!alias || !repoId) return null;
                  const modelUrl = `https://huggingface.co/${repoId}`;
                  return (
                    <div
                      key={`huggingface:${alias}:${repoId}`}
                      className="model-register-item"
                    >
                      <span className="model-register-item-main">{alias}</span>
                      <span className="model-register-item-type">
                        {modelType} / {runtime}
                      </span>
                      <a
                        className="model-register-item-path"
                        href={modelUrl}
                        target="_blank"
                        rel="noreferrer"
                        title={modelUrl}
                      >
                        {repoId}
                      </a>
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => handleUnregisterHfModel(alias)}
                        disabled={registerHfBusy}
                        title="Remove Hugging Face model from personal catalog"
                      >
                        Remove
                      </button>
                    </div>
                  );
                })}
            </div>
          )}
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
                  <span>{registerModelMessage}</span>
                  {registerModelExplanation ? (
                    <StateInspector
                      title={getStateExplanationTitle(
                        registerModelExplanation.detail,
                        "Why this model message is shown",
                      )}
                      summary={getStateExplanationSummary(
                        registerModelExplanation.detail,
                        "The model library action returned structured source and ownership details.",
                      )}
                      rows={buildModelDeleteLockInspectorRows(
                        registerModelExplanation.detail,
                        registerModelExplanation.model,
                      )}
                      ariaLabel="Explain model library message"
                    />
                  ) : null}
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

          <div className="settings-models-grid">
            <div className="settings-subcard">
              <div className="settings-subcard-header">
                <div>
                  <h3>Language runtime</h3>
                  <p className="settings-subcard-copy">
                    Primary response model, API/local lane, and tool-formatting defaults.
                  </p>
                </div>
              </div>

              {renderModelField(
                "Language Model",
                "transformer_model",
                suggestedLangModels,
              )}
              <div
                className={`model-inline-panel runtime-inline-panel ${languageRuntimePanelClass}`.trim()}
              >
                  <div className="runtime-inline-header">
                    <div>
                      <h4>Runtime</h4>
                      <p className="status-note form-note">
                        {languageRuntimeDescription}
                      </p>
                    </div>
                    {(settings.mode === "server" || settings.mode === "local") && (
                      <div className="runtime-inline-actions">
                      {settings.mode === "server" ? (
                        <button
                          type="button"
                          className="runtime-inline-btn"
                          onClick={() => refreshServerRuntime({ refresh: true })}
                          disabled={serverRuntimeLoading || !settings.server_url}
                        >
                          {serverRuntimeLoading ? "Refreshing..." : "Refresh"}
                        </button>
                      ) : managedLocalRuntimeSelected ? (
                        <button
                          type="button"
                          className="runtime-inline-btn"
                          onClick={() => refreshProviderRuntime(false, { refresh: true })}
                          disabled={providerRuntimeLoading}
                        >
                          {providerRuntimeLoading ? "Refreshing..." : "Refresh"}
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="runtime-inline-btn"
                            onClick={() => refreshLocalRuntime()}
                            disabled={localRuntimeLoading}
                          >
                            {localRuntimeLoading ? "Refreshing..." : "Refresh"}
                          </button>
                          <button
                            type="button"
                            className="runtime-inline-btn"
                            onClick={() => runLocalRuntimeAction("load")}
                            disabled={localRuntimeLoadDisabled}
                            title={localRuntimeLoadBlockedReason || undefined}
                          >
                            {localRuntimeActionBusy === "load" ? "Loading..." : "Load"}
                          </button>
                          <button
                            type="button"
                            className="runtime-inline-btn"
                            onClick={() => runLocalRuntimeAction("unload")}
                            disabled={localRuntimeActionBusy === "unload"}
                          >
                            {localRuntimeActionBusy === "unload" ? "Unloading..." : "Unload"}
                          </button>
                        </>
                      )}
                      {settings.mode === "local" && (
                        <button
                          type="button"
                          className="runtime-inline-btn"
                          onClick={() => setLanguageRuntimeCollapsed((prev) => !prev)}
                          aria-controls="settings-language-runtime-details"
                          aria-expanded={!languageRuntimeCollapsed}
                        >
                          {languageRuntimeCollapsed ? "Expand" : "Collapse"}
                        </button>
                      )}
                      </div>
                    )}
                  </div>
                  {(settings.mode !== "local" || languageRuntimeCollapsed) && (
                    <>
                    <div
                    className="runtime-inline-summary"
                    role="group"
                    aria-label="Language runtime summary"
                    aria-busy={languageRuntimeBusy}
                    aria-live="polite"
                  >
                    <span
                      className={`runtime-inline-chip runtime-inline-chip--${languageRuntimeContract.lane}`}
                      aria-label={`Runtime lane: ${languageRuntimeLaneLabel}`}
                    >
                      Lane: {languageRuntimeLaneLabel}
                    </span>
                    <span
                      className="runtime-inline-chip"
                      aria-label={`Runtime model: ${languageRuntimeContract.model || "none selected"}`}
                    >
                      Model: {languageRuntimeContract.model || "none selected"}
                    </span>
                    {(languageRuntimeContract.endpoint ||
                      languageRuntimeContract.lane === RUNTIME_PANEL_LANES.SERVER_LAN ||
                      languageRuntimeContract.lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER) && (
                      <span
                        className="runtime-inline-chip"
                        aria-label={`Runtime endpoint: ${languageRuntimeContract.endpoint || "not configured"}`}
                        title={languageRuntimeContract.endpoint || undefined}
                      >
                        Endpoint: {languageRuntimeContract.endpoint || "not configured"}
                      </span>
                    )}
                    <span
                      className={`runtime-inline-chip runtime-inline-chip--${languageRuntimeContract.availability}`}
                      aria-label={`Runtime availability: ${languageRuntimeContract.availability}`}
                    >
                      Availability: {languageRuntimeContract.availability}
                    </span>
                    </div>
                  {languageRuntimeContract.lastOperation ? (
                    <p
                      className={`status-note form-note${
                        languageRuntimeContract.lastOperation.status === "failed"
                          ? " warn"
                          : ""
                      }`}
                      title={languageRuntimeContract.lastOperation.title}
                      aria-label="Latest runtime operation"
                    >
                      {languageRuntimeContract.lastOperation.label}
                    </p>
                  ) : null}
                    </>
                  )}
                  {settings.mode === "local" && !languageRuntimeCollapsed && (
                    <div
                      id="settings-language-runtime-details"
                      className="runtime-inline-body"
                      aria-busy={languageRuntimeBusy}
                    >
                      {managedLocalRuntimeSelected ? (
                        <>
                          <p className="status-note form-note">
                            Loaded model: {providerRuntime?.loaded_model || "none"}
                          </p>
                          {providerRuntime?.base_url ? (
                            <p className="status-note form-note">
                              Endpoint: {providerRuntime.base_url}
                            </p>
                          ) : null}
                          {providerCliWarning ? (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              {providerCliWarning}
                            </div>
                          ) : null}
                          {providerRuntimeError && (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              {providerRuntimeError}
                            </div>
                          )}
                          {providerActionMessage && (
                            <div className="runtime-inline-message">
                              {providerActionMessage}
                            </div>
                          )}
                        </>
                      ) : (
                        <>
                          {localRuntime?.supports_images ? (
                            <p className="status-note form-note">Capabilities: vision.</p>
                          ) : null}
                          {localRuntime?.effective_model_id &&
                          normalizeModelId(localRuntime.effective_model_id) !==
                            normalizeModelId(settings.transformer_model) ? (
                            <p className="status-note form-note">
                              Loaded model: {localRuntime.effective_model_id}
                            </p>
                          ) : null}
                          {localRuntime?.active_backend ? (
                            <p className="status-note form-note">
                              Backend: {localRuntime.active_backend}
                              {localRuntime?.local_loader
                                ? ` · loader: ${localRuntime.local_loader}`
                                : ""}
                            </p>
                          ) : null}
                          {localRuntimeTiming ? (
                            <p className="status-note form-note">{localRuntimeTiming}</p>
                          ) : null}
                          {localRuntimePreflight?.python_executable ? (
                            <p className="status-note form-note">
                              Backend Python: {localRuntimePreflight.python_executable}
                            </p>
                          ) : null}
                          {localRuntimePreflight?.missing_packages?.length ? (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              Missing direct-local packages:{" "}
                              {localRuntimePreflight.missing_packages.join(", ")}.
                            </div>
                          ) : null}
                          {localRuntimeError && (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              {localRuntimeError}
                            </div>
                          )}
                          {localRuntime?.load_error && (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              {localRuntime.load_error}
                            </div>
                          )}
                          {!localRuntime?.load_error && localRuntimePreflight?.hint && (
                            <div className="runtime-inline-message runtime-inline-message--error">
                              {localRuntimePreflight.hint}
                            </div>
                          )}
                          {localRuntimeMessage && (
                            <div className="runtime-inline-message">
                              {localRuntimeMessage}
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
              </div>

              <div className={`settings-toggle-card${harmonyWarning ? " settings-toggle-card--warn" : ""}`}>
                <div className="settings-toggle-copy">
                  <div className="settings-toggle-title">
                    <span>Harmony Formatting</span>
                    {harmonyWarning && (
                      <span
                        className="status-dot warn label-dot"
                        title={harmonyWarningMessage}
                        role="img"
                        aria-label={harmonyWarningMessage}
                      />
                    )}
                  </div>
                  <p className="status-note form-note">
                    Keeps GPT-OSS tool metadata intact. Auto follows the selected language model.
                  </p>
                  {harmonyWarning && harmonyWarningMessage && (
                    <div className="status-note warn form-note" role="note">
                      {harmonyWarningMessage}
                    </div>
                  )}
                </div>
                <div className="settings-toggle-controls">
                  <label className="settings-switch-row" htmlFor="harmony-mode-select">
                    <span>Mode</span>
                    <select
                      id="harmony-mode-select"
                      value={harmonyMode}
                      onChange={handleHarmonyModeChange}
                      title="Auto enables Harmony only when the selected language model is GPT-OSS."
                    >
                      <option value="auto">Auto</option>
                      <option value="enabled">Enabled</option>
                      <option value="disabled">Disabled</option>
                    </select>
                  </label>
                  <div className="status-note form-note">
                    Backend routing only applies Harmony to GPT-OSS, even when enabled.
                  </div>
                </div>
              </div>
            </div>

            <div className="settings-subcard">
              <div className="settings-subcard-header">
                <div className="settings-heading-with-help">
                  <h3>Speech</h3>
                  <SettingsInfoTip
                    label="About speech models"
                    text="Chat microphone transcription, synthesis, and TTS voices are configured here. API transcription can use the selected OpenAI model; local transcription begins after recording stops."
                  />
                </div>
              </div>

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
            </div>

            <div className="settings-subcard settings-subcard--wide">
              <div className="settings-subcard-header">
                <div className="settings-heading-with-help">
                  <h3>Live streaming</h3>
                  <SettingsInfoTip
                    label="About live streaming"
                    text="The API lane uses OpenAI Realtime. The local lane defaults to the configured Gemma 4 response and multimodal models. Transport, response, and visual models remain separate."
                  />
                </div>
                <div className="settings-model-heading-meta">
                  {renderLaneSelector(
                    "live streaming",
                    liveStreamingLaneKey,
                    ["local", "api"],
                    (nextLaneKey) => {
                      commitSettingValue(
                        "stream_backend",
                        nextLaneKey === "api" ? "api" : "local",
                      );
                    },
                  )}
                  {renderCapabilityStrip(
                    liveStreamingCapabilityField,
                    liveStreamingCapabilityValue,
                  )}
                </div>
              </div>
              {liveStreamingLaneKey === "api" ? (
                <>
                  <label title={fieldTooltips.realtime_model}>
                    Realtime model
                  </label>
                  <input
                    name="realtime_model"
                    value={settings.realtime_model || ""}
                    onChange={handleChange}
                    list="realtime-model-options"
                    placeholder="gpt-realtime-2.1"
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
                    className={realtimeVoiceIsKnown ? "" : "field-select--warn"}
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
                  {!realtimeVoiceIsKnown && (
                    <p className="status-note warn form-note">
                      The current live voice is not a supported OpenAI Realtime
                      voice. Speech models like Voxtral do not belong in this
                      selector.
                    </p>
                  )}

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

                  <SettingsInfoTip
                    label="Realtime connection details"
                    text="The backend mints a short-lived client secret and the browser connects over WebRTC. Realtime may inherit a compatible API transcription model; normal chat microphone recordings still use the speech model after recording stops."
                  />
                </>
              ) : (
                <>
                  <label title={fieldTooltips.stream_backend}>
                    Local transport
                  </label>
                  <select
                    name="stream_backend"
                    value={localLiveTransportValue}
                    onChange={handleChange}
                    title={fieldTooltips.stream_backend}
                  >
                    <option value="local">Float local bridge</option>
                    <option value="livekit">LiveKit room</option>
                  </select>

                  <label title={fieldTooltips.live_agent_mode}>
                    Live agent mode
                  </label>
                  <select
                    name="live_agent_mode"
                    value={settings.live_agent_mode || "local"}
                    onChange={handleChange}
                    title={fieldTooltips.live_agent_mode}
                  >
                    <option value="local">Local</option>
                    <option value="server">Server / LAN</option>
                    <option value="api">API</option>
                  </select>

                  <label title={fieldTooltips.live_agent_model}>
                    Live agent model
                  </label>
                  <input
                    name="live_agent_model"
                    value={settings.live_agent_model || ""}
                    onChange={handleChange}
                    list="live-bridge-model-options"
                    placeholder="gemma-4-E4B-it"
                    title={fieldTooltips.live_agent_model}
                  />
                  <div className="model-row-trailing">
                    {renderCapabilityStrip(
                      "live_agent_model",
                      settings.live_agent_model,
                    )}
                  </div>

                  <label title={fieldTooltips.live_multimodal_model}>
                    Live multimodal model
                  </label>
                  <input
                    name="live_multimodal_model"
                    value={settings.live_multimodal_model || ""}
                    onChange={handleChange}
                    list="live-bridge-model-options"
                    placeholder="gemma-4-E4B-it"
                    title={fieldTooltips.live_multimodal_model}
                  />
                  <div className="model-row-trailing">
                    {renderCapabilityStrip(
                      "live_multimodal_model",
                      settings.live_multimodal_model,
                    )}
                  </div>

                  <datalist id="live-bridge-model-options">
                    {liveBridgeModelOptions.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>

                  <SettingsInfoTip
                    label="Local live connection details"
                    text="The agent model generates responses; the multimodal model handles camera or screen context when needed. Float local bridge is the default persistent transport, while LiveKit remains available for an intentional legacy room server."
                  />
                  {settings.stream_backend === "livekit" && (
                    <p className="status-note form-note">
                      LiveKit is currently selected inside the Local lane. The
                      live agent and multimodal model fields still describe the
                      non-Realtime runtime returned in session metadata.
                    </p>
                  )}
                </>
              )}

            <div className="settings-toggle-stack">
              <label className="settings-toggle-row">
                <input
                  type="checkbox"
                  checked={state.liveTranscriptEnabled !== false}
                  onChange={(event) =>
                    setState((prev) => ({
                      ...prev,
                      liveTranscriptEnabled: event.target.checked,
                    }))
                  }
                />
                <span>Show live transcript</span>
              </label>
              <label className="settings-toggle-row">
                <input
                  type="checkbox"
                  checked={state.liveCameraDefaultEnabled === true}
                  onChange={(event) =>
                    setState((prev) => ({
                      ...prev,
                      liveCameraDefaultEnabled: event.target.checked,
                    }))
                  }
                />
                <span>Start camera automatically</span>
              </label>
              <SettingsInfoTip
                label="About live preferences"
                text="Transcript and camera-start preferences save automatically. Backend and model changes still use the main Save button."
              />
            </div>
          </div>

            <div className="settings-subcard settings-subcard--wide">
              <div className="settings-subcard-header">
                <div className="settings-heading-with-help">
                  <h3>Retrieval &amp; vision</h3>
                  <SettingsInfoTip
                    label="About retrieval and vision"
                    text="Image understanding can use a cloud or local vision model. CLIP is a separate local similarity model for finding related images; text embeddings do not replace it."
                  />
                </div>
              </div>

              {renderModelField(
                "Image understanding fallback",
                "vision_model",
                suggestedVisionModels,
              )}

              <div className="settings-model-block">
                <div className="settings-model-heading">
                  <label
                    className="settings-model-heading-main"
                    htmlFor="rag-embedding-model"
                    title="Text embedding model used for semantic search (RAG)."
                  >
                    RAG embedding model
                  </label>
                  <div className="settings-model-heading-meta">
                    {renderLaneSelector(
                      "rag_embedding_model",
                      embeddingLaneKey,
                      ragEmbeddingLaneOptions,
                      (nextLaneKey) => {
                        const nextPreset = ragEmbeddingPresets.find(
                          (preset) =>
                            (getModelLaneMeta(
                              "rag_embedding_model",
                              preset.value,
                            )?.key || "local") === nextLaneKey,
                        );
                        if (nextPreset) {
                          commitSettingValue("rag_embedding_model", nextPreset.value);
                        }
                      },
                    )}
                    {ragEmbeddingLaneOptions.length <= 1 && (
                      <span className={`model-lane-pill model-lane-pill--${embeddingLaneKey}`}>
                        {getLaneDisplayLabel(embeddingLaneKey)}
                      </span>
                    )}
                  </div>
                </div>
                <div
                  className={`model-select-row model-present model-lane-${embeddingLaneKey}`}
                >
                  <select
                    id="rag-embedding-model"
                    name="rag_embedding_model"
                    value={settings.rag_embedding_model || ""}
                    onChange={handleChange}
                    title="Text embedding model used for semantic search (RAG)."
                  >
                    {visibleRagEmbeddingPresets.map((preset) => (
                      <option key={preset.value} value={preset.value}>
                        {preset.label}
                      </option>
                    ))}
                    {settings.rag_embedding_model &&
                      !visibleRagEmbeddingPresets.some(
                        (preset) => preset.value === settings.rag_embedding_model,
                      ) && (
                        <option value={settings.rag_embedding_model}>
                          {settings.rag_embedding_model}
                        </option>
                      )}
                  </select>
                  <div className="model-row-trailing">
                    {renderCapabilityStrip(
                      "rag_embedding_model",
                      settings.rag_embedding_model,
                    )}
                  </div>
                </div>
              </div>

              <p className="status-note">
                Values starting with <code>local:</code> attempt to use on-device
                embeddings. <code>api:</code> entries use the configured API
                embedding provider and should be reserved for non-sensitive scopes.
              </p>
              {String(settings.rag_embedding_model || "").includes("embeddinggemma") && (
                <p className="status-note form-note">
                  EmbeddingGemma is included as an opt-in review preset. It is a
                  text embedding path only, and local loading may need newer
                  Sentence Transformers support than the current default stack.
                </p>
              )}
              <div
                className={`model-inline-panel runtime-inline-panel runtime-inline-panel--${embeddingLaneKey}`}
              >
                <div className="runtime-inline-header">
                  <div>
                    <h4>Embeddings runtime</h4>
                    <p className="status-note form-note">
                      Load and unload the text embedding runtime without leaving the models view.
                    </p>
                  </div>
                  <div className="runtime-inline-actions">
                    <button
                      type="button"
                      className="runtime-inline-btn"
                      onClick={() => refreshStatus()}
                      disabled={ragState === "loading"}
                    >
                      {ragState === "loading" ? "Refreshing..." : "Refresh"}
                    </button>
                    {embeddingLaneKey !== "api" && (
                      <>
                        <button
                          type="button"
                          className="runtime-inline-btn"
                          onClick={() => runEmbeddingRuntimeAction("load")}
                          disabled={embeddingRuntimeBusy === "load"}
                        >
                          {embeddingRuntimeBusy === "load" ? "Loading..." : "Load"}
                        </button>
                        <button
                          type="button"
                          className="runtime-inline-btn"
                          onClick={() => runEmbeddingRuntimeAction("unload")}
                          disabled={embeddingRuntimeBusy === "unload"}
                        >
                          {embeddingRuntimeBusy === "unload" ? "Unloading..." : "Unload"}
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      className="runtime-inline-btn"
                      onClick={() => setEmbeddingRuntimeCollapsed((prev) => !prev)}
                      aria-expanded={!embeddingRuntimeCollapsed}
                    >
                      {embeddingRuntimeCollapsed ? "Expand" : "Collapse"}
                    </button>
                  </div>
                </div>
                {!embeddingRuntimeCollapsed && (
                  <div
                    className="runtime-inline-body"
                    aria-busy={ragState === "loading" || !!embeddingRuntimeBusy}
                  >
                    <div className="runtime-inline-summary">
                      <span className={`runtime-inline-chip runtime-inline-chip--${embeddingLaneKey}`}>
                        {settings.rag_embedding_model || "simple"}
                      </span>
                      <span className="runtime-inline-chip">
                        {embeddingRuntime?.state || (embeddingLaneKey === "api" ? "remote" : "idle")}
                      </span>
                      {(ragState === "loading" || embeddingRuntimeBusy) && (
                        <span className="runtime-inline-chip runtime-inline-chip--busy">
                          {embeddingRuntimeBusy ? `${embeddingRuntimeBusy}…` : "refreshing…"}
                        </span>
                      )}
                    </div>
                    <p className="status-note form-note">
                      Mode: {embeddingRuntime?.mode || (embeddingLaneKey === "api" ? "api" : "local")}
                    </p>
                    {embeddingRuntime?.error && (
                      <div className="runtime-inline-message runtime-inline-message--error">
                        {embeddingRuntime.error}
                      </div>
                    )}
                    {embeddingRuntimeMessage && (
                      <div className="runtime-inline-message">
                        {embeddingRuntimeMessage}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="settings-model-block">
                <div className="settings-model-heading">
                  <label
                    className="settings-model-heading-main"
                    htmlFor="rag-clip-model"
                    title="CLIP model used for image-aware RAG retrieval."
                  >
                    Image retrieval model (CLIP)
                  </label>
                  <SettingsInfoTip
                    label="About CLIP retrieval"
                    text="CLIP compares images for retrieval. It is local and distinct from the cloud or local model that interprets an image for chat."
                  />
                  <div className="settings-model-heading-meta">
                    <span className="model-lane-pill model-lane-pill--local">Local</span>
                  </div>
                </div>
                <div className="model-select-row model-present model-lane-local">
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
                  <div className="model-row-trailing">
                    {renderCapabilityStrip("rag_clip_model", settings.rag_clip_model)}
                  </div>
                </div>
              </div>

              <label
                htmlFor="rag-chat-min-similarity"
                title="Minimum similarity (0-1) for automatic RAG injection."
              >
                RAG min similarity
                <SettingsInfoTip
                  label="About similarity filtering"
                  text="Lower values include more matches. Set this to 0 to disable similarity filtering."
                />
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
            </div>
          </div>

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

          <label className="settings-toggle-row" title="Cache attention keys/values to speed up generation">
            <input
              name="kv_cache"
              type="checkbox"
              checked={settings.kv_cache}
              onChange={handleChange}
            />
            <span>Enable K/V cache</span>
          </label>

          <label className="settings-toggle-row" title="Allow model to spill to system RAM when VRAM is low">
            <input
              name="ram_swap"
              type="checkbox"
              checked={settings.ram_swap}
              onChange={handleChange}
            />
            <span>Enable RAM swap</span>
          </label>

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

              <label
                className="settings-toggle-row advanced-grid-span"
                title="Attempt to enable Flash Attention when dependencies exist"
              >
                <input
                  name="flash_attention"
                  type="checkbox"
                  checked={!!settings.flash_attention}
                  onChange={handleChange}
                />
                <span>Enable Flash Attention</span>
              </label>

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

          <label
            className="settings-toggle-row"
            title="Add a custom models directory to the search path. Default storage is data/models; repo-root models is treated as a legacy search location."
          >
            <input
              name="use_custom_models_folder"
              type="checkbox"
              checked={useCustomModelsFolder}
              onChange={(e) => setUseCustomModelsFolder(!!e.target.checked)}
            />
            <span>Use custom models folder</span>
            <SettingsInfoTip
              label="About model folders"
              text="Default downloads use data/models. The repo-root models folder is scanned only as a legacy or bundled location."
            />
          </label>

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

          <label className="settings-toggle-row" title="Use an explicit conversations directory instead of default (Default: ./data/conversations)">
            <input
              name="use_custom_conv_folder"
              type="checkbox"
              checked={useCustomConvFolder}
              onChange={(e) => setUseCustomConvFolder(!!e.target.checked)}
            />
            <span>Use custom conversations folder</span>
          </label>

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
            <div className="settings-heading-with-help">
              <h3>Appearance</h3>
              <SettingsInfoTip
                label="About themes"
                text="The top bar controls dark or light mode; this setting selects the color family beneath it. Built-in themes ship with Float, while custom themes are stored in the data folder."
              />
            </div>
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
              {visualThemeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {`${option.label} (${isBuiltInVisualTheme(option.value) ? "built-in" : "custom"})`}
                </option>
              ))}
            </select>
            <div className="settings-action-row">
              <button
                type="button"
                className="icon-btn"
                onClick={openNewThemeEditor}
                style={{ marginTop: 0 }}
              >
                Add new theme
              </button>
              {selectedCustomTheme && (
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => loadThemeDraft(selectedCustomTheme)}
                  style={{ marginTop: 0 }}
                >
                  Edit selected theme
                </button>
              )}
            </div>
            {showThemeEditor && (
              <div className="theme-editor-card">
                <label className="field-label" htmlFor="theme-draft-label">
                  <span>Theme name</span>
                </label>
                <input
                  id="theme-draft-label"
                  type="text"
                  value={themeDraftLabel}
                  onChange={(event) => setThemeDraftLabel(event.target.value)}
                  placeholder="Custom Theme"
                />
                <div className="theme-slot-grid">
                  {THEME_SLOT_KEYS.map((slotKey) => (
                    <label key={slotKey} className="theme-slot-control">
                      <span>{THEME_SLOT_LABELS[slotKey]}</span>
                      <input
                        type="color"
                        value={themeDraftSlots[slotKey] || "#000000"}
                        onChange={(event) =>
                          setThemeDraftSlots((prev) => ({
                            ...prev,
                            [slotKey]: event.target.value,
                          }))
                        }
                      />
                      <code>{themeDraftSlots[slotKey] || "#000000"}</code>
                    </label>
                  ))}
                </div>
                <div className="inline-flex" style={{ gap: 10, marginTop: 12, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={saveThemeDraft}
                    disabled={themeSaveBusy}
                    style={{ marginTop: 0 }}
                  >
                    {themeSaveBusy ? "Saving..." : isEditingCustomTheme ? "Save changes" : "Save theme"}
                  </button>
                  {isEditingCustomTheme && (
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={deleteThemeDraft}
                      disabled={themeDeleteBusy}
                      style={{ marginTop: 0 }}
                    >
                      {themeDeleteBusy ? "Deleting..." : "Delete theme"}
                    </button>
                  )}
                  {!selectedCustomTheme && (
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={closeThemeEditor}
                      style={{ marginTop: 0 }}
                    >
                      Cancel
                    </button>
                  )}
                </div>
                {themeMessage && <p className="status-note" style={{ marginTop: 8 }}>{themeMessage}</p>}
              </div>
            )}
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
            <div className="settings-action-row">
              <button
                type="button"
                className="icon-btn settings-action-btn"
                onClick={handleActionHistorySave}
                disabled={actionHistorySaving}
              >
                {actionHistorySaving ? "Saving..." : "Save work history"}
              </button>
              <Link
                to="/work-history"
                className="icon-btn settings-action-btn"
              >
                Open work history
              </Link>
            </div>
            {actionHistoryMessage && <p className="status-note">{actionHistoryMessage}</p>}
          </div>

          <div className="settings-section">
            <div className="settings-header">
              <div className="settings-heading-with-help">
                <h3>Tools</h3>
                <SettingsInfoTip
                  label="About tool access"
                  text="Workflow availability controls where a tool appears. Approval controls whether it can run automatically. Expand the catalog only when you need per-tool overrides."
                />
              </div>
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
            <details className="tool-browser-disclosure">
              <summary>
                <span>Sources &amp; runtime limits</span>
                <span className="tool-browser-summary-count">
                  {toolSourceCards.length + 1} sources
                </span>
              </summary>
              <div className="tool-browser-disclosure-body">
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
              </div>
            </details>
            <details className="tool-browser-disclosure">
              <summary>
                <span>Tool catalog</span>
                <span className="tool-browser-summary-count">
                  {toolCatalogLoading
                    ? "loading"
                    : `${filteredToolCatalog.length} shown · ${toolStatusSummary.live} live`}
                </span>
              </summary>
              <div className="tool-browser-disclosure-body">
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
            {toolPolicyMessage && (
              <p className="status-note" style={{ marginTop: 6 }}>
                {toolPolicyMessage}
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
                  const policy = normalizeToolPolicy(entry?.policy);
                  const workflowSelectId = toolPolicyControlId(entry?.id, "workflow");
                  const approvalSelectId = toolPolicyControlId(entry?.id, "approval");
                  const policySaving =
                    toolPolicySaving === `${String(entry?.id || "")}:workflow` ||
                    toolPolicySaving === `${String(entry?.id || "")}:approval`;
                  return (
                    <details key={entry.id} className="tool-browser-card">
                      <summary className="status-header">
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
                      </summary>
                      <div className="tool-browser-card-body">
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
                      <div className="tool-browser-policy-grid">
                        <label htmlFor={workflowSelectId}>
                          <span className="tool-browser-label">Workflow availability</span>
                          <select
                            id={workflowSelectId}
                            value={policy.workflow}
                            aria-label={`${entry.display_name || entry.id} workflow availability`}
                            disabled={policySaving}
                            onChange={(event) =>
                              handleToolPolicyChange(entry.id, "workflow", event.target.value)
                            }
                          >
                            {TOOL_WORKFLOW_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label htmlFor={approvalSelectId}>
                          <span className="tool-browser-label">Approval requirement</span>
                          <select
                            id={approvalSelectId}
                            value={policy.approval}
                            aria-label={`${entry.display_name || entry.id} approval requirement`}
                            disabled={policySaving}
                            onChange={(event) =>
                              handleToolPolicyChange(entry.id, "approval", event.target.value)
                            }
                          >
                            {TOOL_APPROVAL_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="tool-browser-policy-state">
                          <span className="tool-browser-chip">
                            Text {policy.workflows.text ? "on" : "off"}
                          </span>
                          <span className="tool-browser-chip">
                            Live {policy.workflows.live ? "on" : "off"}
                          </span>
                          {policy.workflows.live && (
                            <span className="tool-browser-chip">
                              {policy.live_auto ? "Live auto" : "Live gated"}
                            </span>
                          )}
                        </div>
                      </div>
                      {policy.live_unavailable_reason && policy.workflows.live && (
                        <p className="status-note" style={{ marginTop: 6 }}>
                          {policy.live_unavailable_reason === "client_resolution_required"
                            ? "Live mode can see this tool setting, but it still needs the client-resolution bridge before realtime can call it directly."
                            : "Live mode keeps this out of the realtime tool list while it is marked high approval."}
                        </p>
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
                      </div>
                    </details>
                  );
                })}
              </div>
            )}
              </div>
            </details>
          </div>

            </section>
          )}

          {showSettingsSection("workflows") && (
            <section
              id="settings-workflows"
              className="settings-card settings-section"
              aria-label="Visual data and privacy"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Visual Data &amp; Privacy</h2>
                  <p className="settings-card-copy">
                    Control temporary camera and screen images produced by Live mode and
                    computer-use tools, plus privacy checks before model access.
                  </p>
                </div>
              </div>

              <div className="settings-subcard settings-subcard--wide">
                <div className="settings-subcard-header">
                  <div>
                    <h3>Skills &amp; workflows live in Knowledge</h3>
                    <p className="settings-subcard-copy">
                      Manage workflow defaults, capability modules, and local markdown skill
                      documents in their dedicated workspace.
                    </p>
                  </div>
                  <Link
                    className="icon-btn settings-inline-link"
                    to="/knowledge?tab=skills"
                    title="Open Skills and workflows in Knowledge"
                  >
                    Open Skills &amp; workflows
                  </Link>
                </div>
              </div>

              <div className="settings-section">
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
                  title={getSensitivityTooltip(captureDefaultSensitivity)}
                >
                  Default capture sensitivity
                </label>
                <select
                  id="capture-sensitivity"
                  value={captureDefaultSensitivity}
                  onChange={(event) => setCaptureDefaultSensitivity(event.target.value)}
                  title={getSensitivityTooltip(captureDefaultSensitivity)}
                >
                  {CAPTURE_SENSITIVITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <div className="capture-privacy-options">
                  <label className="capture-privacy-option">
                    <input
                      type="checkbox"
                      checked={captureAllowModelRawImageAccess}
                      onChange={(event) =>
                        setCaptureAllowModelRawImageAccess(event.target.checked)
                      }
                    />
                    <span>Allow raw image access for supported models</span>
                  </label>
                  <label className="capture-privacy-option">
                    <input
                      type="checkbox"
                      checked={captureAllowSummaryFallback}
                      onChange={(event) => setCaptureAllowSummaryFallback(event.target.checked)}
                    />
                    <span>Allow summary fallback when raw images are restricted</span>
                  </label>
                </div>
                <p className="status-note" style={{ marginTop: 6 }}>
                  Computer observations, camera captures, and screen stills stay transient for
                  this window unless promoted. Promoted captures remain accessible as durable
                  attachments.
                </p>

                <label
                  className="field-label"
                  htmlFor="privacy-filter-mode"
                  title="Automatic first-pass text privacy classification for saved memories, conversations, knowledge, and file writes."
                >
                  Text privacy filter on writes
                </label>
                <select
                  id="privacy-filter-mode"
                  value={privacyFilterMode}
                  onChange={(event) =>
                    setPrivacyFilterMode(normalizePrivacyFilterMode(event.target.value))
                  }
                >
                  {PRIVACY_FILTER_MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="status-note" style={{ marginTop: 6 }}>
                  {
                    PRIVACY_FILTER_MODE_OPTIONS.find(
                      (option) => option.value === normalizePrivacyFilterMode(privacyFilterMode),
                    )?.description
                  }{" "}
                  Uses a local text classifier. Image access stays controlled separately above.
                </p>

                <label
                  className="field-label"
                  htmlFor="privacy-filter-model"
                  title="Text classifier used by the privacy filter. The download manager lists privacy-filter as the local download alias."
                >
                  Privacy filter model
                </label>
                <input
                  id="privacy-filter-model"
                  value={privacyFilterModel}
                  onChange={(event) => setPrivacyFilterModel(event.target.value)}
                  list="privacy-filter-model-presets"
                  placeholder="openai/privacy-filter"
                  title="Hugging Face model id or local alias for the text privacy classifier."
                />
                <datalist id="privacy-filter-model-presets">
                  {PRIVACY_FILTER_MODEL_PRESETS.map((preset) => (
                    <option key={preset.value} value={preset.value}>
                      {preset.label}
                    </option>
                  ))}
                </datalist>
                <p className="status-note" style={{ marginTop: 6 }}>
                  This is not a RAG embedding model. Use Downloads in Models to fetch{" "}
                  <code>privacy-filter</code> locally before enabling always-on checks.
                </p>

                <label
                  className="field-label"
                  htmlFor="privacy-route-mode"
                  title="Optional preflight before sending protected or secret text to a non-local model."
                >
                  Private message rerouting
                </label>
                <select
                  id="privacy-route-mode"
                  value={privacyRouteMode}
                  onChange={(event) =>
                    setPrivacyRouteMode(normalizePrivacyRouteMode(event.target.value))
                  }
                >
                  {PRIVACY_ROUTE_MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="status-note" style={{ marginTop: 6 }}>
                  {
                    PRIVACY_ROUTE_MODE_OPTIONS.find(
                      (option) => option.value === normalizePrivacyRouteMode(privacyRouteMode),
                    )?.description
                  }{" "}
                  The route is never automatic; it uses the same accept, edit, or deny review card.
                </p>

                <div className="inline-flex" style={{ gap: 10, marginTop: 12, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={handleCapturePrivacySave}
                    disabled={capturePrivacySaving}
                    style={{ marginTop: 0 }}
                  >
                    {capturePrivacySaving ? "Saving..." : "Save capture & privacy settings"}
                  </button>
                </div>
                {capturePrivacyMessage && (
                  <p className="status-note" role="status">{capturePrivacyMessage}</p>
                )}
              </div>
            </section>
          )}


          {showSettingsSection("background") && (
            <section
              id="settings-background"
              className="settings-card settings-section"
              aria-label="Background autonomy"
            >
              <div className="settings-card-header">
                <div>
                  <h2>Background Processing</h2>
                  <p className="settings-card-copy">
                    Bounded autonomy settings for reflection review, overnight runs,
                    and separate long-running container checks.
                  </p>
                </div>
              </div>

              <div className="settings-section">
                <label
                  className="checkbox-row"
                  style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
                >
                  <input
                    type="checkbox"
                    name="background_autonomy_enabled"
                    checked={!!settings.background_autonomy_enabled}
                    onChange={handleChange}
                  />
                  <span>Enable background autonomy</span>
                </label>

                <label
                  className="checkbox-row"
                  style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
                  title="Prefer container or execution-session isolation for background and subagent work when a sandbox backend is available."
                >
                  <input
                    type="checkbox"
                    name="background_autonomy_sandbox_processes"
                    checked={settings.background_autonomy_sandbox_processes !== false}
                    onChange={handleChange}
                  />
                  <span>Sandbox background processes</span>
                </label>

                <label
                  className="field-label"
                  htmlFor="background-autonomy-mode"
                  title="Controls how the background autonomy runner decides when to stop."
                >
                  Background autonomy mode
                </label>
                <select
                  id="background-autonomy-mode"
                  name="background_autonomy_mode"
                  value={normalizeBackgroundAutonomyMode(settings.background_autonomy_mode)}
                  onChange={handleChange}
                >
                  {BACKGROUND_AUTONOMY_MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="status-note" style={{ marginTop: 6 }}>
                  {
                    BACKGROUND_AUTONOMY_MODE_OPTIONS.find(
                      (option) =>
                        option.value ===
                        normalizeBackgroundAutonomyMode(settings.background_autonomy_mode),
                    )?.description
                  }
                </p>

                <div className="advanced-grid" style={{ marginTop: 12 }}>
                  <label
                    htmlFor="background-runtime-minutes"
                    title="Default overnight review budget. Extended mode stops by threshold instead."
                  >
                    Runtime budget (minutes)
                  </label>
                  <input
                    id="background-runtime-minutes"
                    type="number"
                    min="1"
                    max="1440"
                    step="1"
                    value={Math.max(
                      1,
                      Math.round(
                        Number(settings.background_autonomy_max_runtime_seconds || 1800) /
                          60,
                      ),
                    )}
                    onChange={(event) => {
                      const parsed = parseInt(event.target.value, 10);
                      commitSettingValue(
                        "background_autonomy_max_runtime_seconds",
                        (Number.isFinite(parsed) ? Math.max(1, parsed) : 30) * 60,
                      );
                    }}
                  />

                  <label
                    htmlFor="background-routine-interval"
                    title="How often the routine runner wakes up outside basic-test mode."
                  >
                    Routine poll interval (minutes)
                  </label>
                  <input
                    id="background-routine-interval"
                    type="number"
                    min="1"
                    max="1440"
                    step="1"
                    value={Math.max(
                      1,
                      Math.round(
                        Number(settings.background_autonomy_interval_seconds || 900) /
                          60,
                      ),
                    )}
                    onChange={(event) => {
                      const parsed = parseInt(event.target.value, 10);
                      commitSettingValue(
                        "background_autonomy_interval_seconds",
                        (Number.isFinite(parsed) ? Math.max(1, parsed) : 15) * 60,
                      );
                    }}
                  />

                  <label
                    htmlFor="background-basic-tick-count"
                    title="Basic mode tick budget. Default is two checks."
                  >
                    Basic test ticks
                  </label>
                  <input
                    id="background-basic-tick-count"
                    name="background_autonomy_basic_tick_count"
                    type="number"
                    min="1"
                    max="20"
                    step="1"
                    value={settings.background_autonomy_basic_tick_count ?? 2}
                    onChange={handleChange}
                  />

                  <label
                    htmlFor="background-basic-tick-minutes"
                    title="Basic mode interval. Default is five minutes."
                  >
                    Basic tick interval (minutes)
                  </label>
                  <input
                    id="background-basic-tick-minutes"
                    type="number"
                    min="1"
                    max="1440"
                    step="1"
                    value={Math.max(
                      1,
                      Math.round(
                        Number(settings.background_autonomy_basic_tick_seconds || 300) /
                          60,
                      ),
                    )}
                    onChange={(event) => {
                      const parsed = parseInt(event.target.value, 10);
                      commitSettingValue(
                        "background_autonomy_basic_tick_seconds",
                        (Number.isFinite(parsed) ? Math.max(1, parsed) : 5) * 60,
                      );
                    }}
                  />

                  <label
                    htmlFor="background-satisfied-threshold"
                    title="Extended mode stops after a run reaches this usefulness/novelty score."
                  >
                    Satisfied threshold
                  </label>
                  <input
                    id="background-satisfied-threshold"
                    name="background_autonomy_satisfied_threshold"
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={settings.background_autonomy_satisfied_threshold ?? 0.8}
                    onChange={handleChange}
                  />

                  <label
                    htmlFor="background-reflection-cap"
                    title="Safety cap for reflection scheduler runs inside one autonomy tick."
                  >
                    Reflection cap per tick
                  </label>
                  <input
                    id="background-reflection-cap"
                    name="background_autonomy_max_reflections_per_tick"
                    type="number"
                    min="0"
                    max="5"
                    step="1"
                    value={settings.background_autonomy_max_reflections_per_tick ?? 1}
                    onChange={handleChange}
                  />

                  <label
                    htmlFor="background-min-priority"
                    title="Lowest reflection priority eligible for background review."
                  >
                    Minimum priority
                  </label>
                  <input
                    id="background-min-priority"
                    name="background_autonomy_min_priority"
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={settings.background_autonomy_min_priority ?? 0.05}
                    onChange={handleChange}
                  />
                </div>

                <div
                  className="inline-flex"
                  style={{ gap: 10, marginTop: 12, flexWrap: "wrap" }}
                >
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={handleSave}
                    disabled={saving || !isDirty}
                    style={{ marginTop: 0 }}
                  >
                    {saving ? "Saving..." : "Save background settings"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={handleBackgroundAutonomyDryRun}
                    disabled={backgroundAutonomyTickBusy}
                    style={{ marginTop: 0 }}
                  >
                    {backgroundAutonomyTickBusy ? "Planning..." : "Dry run tick"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => refreshStatus()}
                    disabled={backgroundAutonomyLoading}
                    style={{ marginTop: 0 }}
                  >
                    {backgroundAutonomyLoading ? "Refreshing..." : "Refresh status"}
                  </button>
                </div>

                {backgroundAutonomyMessage && (
                  <p className="status-note">{backgroundAutonomyMessage}</p>
                )}

                <div className="settings-subcard" style={{ marginTop: 12 }}>
                  <div className="settings-subcard-header">
                    <div>
                      <h3>Autonomy status</h3>
                      <p className="settings-subcard-copy">
                        {backgroundAutonomyStatus?.error
                          ? "Status endpoint is not reachable."
                          : backgroundAutonomyStatus
                            ? `${
                                backgroundAutonomyStatus.routine_enabled
                                  ? "Routine enabled"
                                  : "Manual or disabled"
                              }; ${
                                backgroundAutonomyStatus.reflection?.candidate_count ?? 0
                              } reflection candidate(s).`
                            : "Status has not loaded yet."}
                      </p>
                    </div>
                  </div>
                  <div className="workflow-profile-meta">
                    <span>
                      Mode:{" "}
                      <strong>
                        {normalizeBackgroundAutonomyMode(
                          backgroundAutonomyStatus?.configured_mode ||
                            settings.background_autonomy_mode,
                        ).replace("_", " ")}
                      </strong>
                    </span>
                    <span>
                      Budget:{" "}
                      <strong>
                        {Math.max(
                          1,
                          Math.round(
                            Number(
                              backgroundAutonomyStatus?.max_runtime_seconds ||
                                settings.background_autonomy_max_runtime_seconds ||
                                1800,
                            ) / 60,
                          ),
                        )}{" "}
                        min
                      </strong>
                    </span>
                    <span>
                      Threshold:{" "}
                      <strong>
                        {Number(
                          backgroundAutonomyStatus?.satisfied_threshold ??
                            settings.background_autonomy_satisfied_threshold ??
                            0.8,
                        ).toFixed(2)}
                      </strong>
                    </span>
                  </div>
                </div>

                <p className="status-note" style={{ marginTop: 10 }}>
                  Container orchestration and API background-response checks are kept in
                  a separate opt-in test suite so normal Poetry runs do not stall.
                </p>
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
            <h3>System prompt</h3>
            <label
              className="field-label"
              title="Loaded from backend defaults; this section is not editable."
            >
              Default instructions (read-only)
            </label>
            <textarea
              className="message-field settings-instructions-field"
              rows="16"
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
              className="message-field settings-instructions-field"
              rows="16"
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

          {message && <p className="settings-message">{message}</p>}

          </div>
        </div>

      )}

      {/* Global DownloadTray handles UI overlay; keep Settings minimal here. */}

    </div>

  );

};



export default Settings;

