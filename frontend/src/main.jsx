import React, {
  createContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import "./style.css"; // Global styles
import "./styles/theme.css";
import "@livekit/components-styles";
import axios from "axios";
import { ensureServiceWorker } from "./utils/push";
import { shouldRefreshProviderModels } from "./utils/providerProbe";
import { ensureDeviceAndToken } from "./utils/sync";
import { isGptOssModel, isLocalRuntimeEntry } from "./utils/modelUtils";
import ReactDOM from "react-dom/client";
import App from "./components/App"; // Clean import path
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import {
  applyVisualTheme,
  DEFAULT_VISUAL_THEME,
  getMuiPaletteOptions,
  normalizeVisualTheme,
  registerCustomThemes,
} from "./theme";
import {
  normalizeToolDisplayMode,
  normalizeToolLinkBehavior,
} from "./utils/toolDisplayModes";

// Generate a default conversation name like "New Chat 2024-05-01 13:37"
const generateDefaultName = (timestamp = Date.now()) => {
  const date = new Date(timestamp);
  const pad = (n) => String(n).padStart(2, "0");
  return `New Chat ${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

// Derive a default name from a session id if it follows the `sess-<ts>` pattern
const defaultNameFromId = (id) => {
  const match = /^sess-(\d+)$/.exec(id || "");
  if (match) {
    return generateDefaultName(parseInt(match[1], 10));
  }
  return generateDefaultName();
};

const parseStoredInt = (value) => {
  if (value == null || value === "") return null;
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? null : parsed;
};

const parseStoredConversation = (value) => {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const parseStoredJsonArray = (value) => {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const parseStoredThemes = (value) => {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const normalizeBackendMode = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "local-cloud" || raw === "cloud") return "server";
  if (raw === "local-small" || raw === "local-static") return "local";
  if (raw === "local" || raw === "server" || raw === "api") return raw;
  return "api";
};

const applyBackendRuntimeSelection = (prev, data = {}) => {
  const next = { ...prev };
  let changed = false;
  const nextBackendMode = normalizeBackendMode(data.mode || prev.backendMode);
  if (nextBackendMode !== prev.backendMode) {
    next.backendMode = nextBackendMode;
    changed = true;
  }
  const incomingApiModel =
    typeof data.model === "string" && data.model.trim() ? data.model.trim() : "";
  if (incomingApiModel && incomingApiModel !== prev.apiModel) {
    next.apiModel = incomingApiModel;
    changed = true;
  }
  if (typeof data.server_url === "string" && data.server_url !== prev.serverUrl) {
    next.serverUrl = data.server_url;
    changed = true;
  }
  const incomingTransformer =
    typeof data.transformer_model === "string" ? data.transformer_model.trim() : "";
  if (nextBackendMode === "local" && incomingTransformer) {
    if (incomingTransformer !== prev.localModel) {
      next.localModel = incomingTransformer;
      changed = true;
    }
    if (
      !isLocalRuntimeEntry(incomingTransformer) &&
      incomingTransformer !== prev.transformerModel
    ) {
      next.transformerModel = incomingTransformer;
      changed = true;
    }
  } else if (
    nextBackendMode === "server" &&
    incomingTransformer &&
    !isLocalRuntimeEntry(incomingTransformer) &&
    incomingTransformer !== prev.transformerModel
  ) {
    next.transformerModel = incomingTransformer;
    changed = true;
  }
  return changed ? next : prev;
};

// Create a Context for the global state
export const GlobalContext = createContext();

// Create a Provider component
const GlobalProvider = ({ children }) => {
  const [state, setState] = useState(() => {
    const storedConversation = localStorage.getItem("conversation");
    const storedSessionId = localStorage.getItem("sessionId");
    const storedHistory = localStorage.getItem("history");
    const storedLevel = localStorage.getItem("approvalLevel") || "all";
    const storedTheme = localStorage.getItem("theme") || "light";
    const storedCustomThemes = parseStoredThemes(localStorage.getItem("customThemes"));
    registerCustomThemes(storedCustomThemes);
    const storedVisualTheme = normalizeVisualTheme(
      localStorage.getItem("visualTheme") || DEFAULT_VISUAL_THEME,
    );
    const storedBackendModeRaw =
      localStorage.getItem("backendMode") || "api";
    const storedBackendMode = normalizeBackendMode(storedBackendModeRaw);
    const storedApiModel = localStorage.getItem("apiModel") || "gpt-5.4";
    const storedLocalModel =
      localStorage.getItem("localModel") || "mistral:7b";
    const storedTransformerModel =
      localStorage.getItem("transformerModel") || "gpt-oss-20b";
    const storedStaticModel =
      localStorage.getItem("staticModel") || "gpt-5.4-mini";
    const storedHarmonyFormatRaw = localStorage.getItem("harmonyFormat");
    const storedHarmonyFormat = storedHarmonyFormatRaw === "true";
    const storedHarmonyTouched = localStorage.getItem("harmonyTouched") === "true";
    const storedServerUrl = localStorage.getItem("serverUrl") || "";
    const storedSttModel = localStorage.getItem("sttModel") || "whisper-1";
    const storedTtsModel = localStorage.getItem("ttsModel") || "tts-1";
    const storedVoiceModel = localStorage.getItem("voiceModel") || "alloy";
    const storedLiveTranscriptEnabledRaw = localStorage.getItem(
      "liveTranscriptEnabled",
    );
    const storedLiveTranscriptEnabled =
      storedLiveTranscriptEnabledRaw == null
        ? true
        : storedLiveTranscriptEnabledRaw === "true";
    const storedLiveCameraDefaultEnabledRaw = localStorage.getItem(
      "liveCameraDefaultEnabled",
    );
    const storedLiveCameraDefaultEnabled =
      storedLiveCameraDefaultEnabledRaw === "true";
    const storedWorkflowProfile =
      localStorage.getItem("workflowProfile") || "default";
    const storedCaptureRetentionDays =
      parseStoredInt(localStorage.getItem("captureRetentionDays")) ?? 7;
    const storedCaptureDefaultSensitivity =
      localStorage.getItem("captureDefaultSensitivity") || "personal";
    const storedCaptureAllowModelRawImageAccessRaw = localStorage.getItem(
      "captureAllowModelRawImageAccess",
    );
    const storedCaptureAllowModelRawImageAccess =
      storedCaptureAllowModelRawImageAccessRaw == null
        ? true
        : storedCaptureAllowModelRawImageAccessRaw === "true";
    const storedCaptureAllowSummaryFallbackRaw = localStorage.getItem(
      "captureAllowSummaryFallback",
    );
    const storedCaptureAllowSummaryFallback =
      storedCaptureAllowSummaryFallbackRaw == null
        ? true
        : storedCaptureAllowSummaryFallbackRaw === "true";
    const storedEnabledWorkflowModules = parseStoredJsonArray(
      localStorage.getItem("enabledWorkflowModules"),
    );
    const storedUserTimezone = localStorage.getItem("userTimezone") || "";
    const storedPreferredMicDeviceId =
      localStorage.getItem("preferredMicDeviceId") || "";
    const storedPreferredCameraDeviceId =
      localStorage.getItem("preferredCameraDeviceId") || "";
    const storedMicInputGain =
      parseFloat(localStorage.getItem("micInputGain") || "1") || 1;
    const storedOutputVolume =
      parseFloat(localStorage.getItem("outputVolume") || "1") || 1;
    const storedVisionModel =
      localStorage.getItem("visionModel") || "google/paligemma2-3b-pt-224";
    const storedMaxContextLength =
      parseInt(localStorage.getItem("maxContextLength") || "2048", 10);
    const storedKvCache = localStorage.getItem("kvCache") !== "false";
    const storedRamSwap = localStorage.getItem("ramSwap") === "true";
    const storedWsLastEventAt = parseStoredInt(localStorage.getItem("wsLastEventAt"));
    const storedWsLastErrorAt = parseStoredInt(localStorage.getItem("wsLastErrorAt"));
    const storedWsLastError = localStorage.getItem("wsLastError") || "";
    const storedRequestTimeoutSec = parseStoredInt(localStorage.getItem("requestTimeoutSec"));
    const storedStreamIdleTimeoutSec = parseStoredInt(localStorage.getItem("streamIdleTimeoutSec"));
    const storedToolDisplayMode = normalizeToolDisplayMode(
      localStorage.getItem("toolDisplayMode"),
    );
    const storedToolLinkBehavior = normalizeToolLinkBehavior(
      localStorage.getItem("toolLinkBehavior"),
    );
    const storedThinkingModeRaw = localStorage.getItem("thinkingMode");
    const storedThinkingMode = (() => {
      const raw = storedThinkingModeRaw == null ? "" : String(storedThinkingModeRaw).trim().toLowerCase();
      if (raw === "high") return "high";
      if (raw === "low") return "low";
      if (raw === "auto") return "auto";
      if (raw === "true") return "high";
      if (raw === "false") return "low";
      return "auto";
    })();
    const initialSessionId = storedSessionId || `sess-${Date.now()}`;
    const storedSessionName = localStorage.getItem("sessionName");

    // Determine Harmony default when not explicitly set
    const isHarmonyPreferred = (...models) => {
      try {
        const generic = [
          "gpt-4o",
          "gpt-4.1",
          "gpt-5-mini",
          "gpt-5",
          "gpt-5.1",
          "gpt-5.2",
        ];
        return models
          .filter(Boolean)
          .map((model) => String(model).toLowerCase())
          .some((m) => isGptOssModel(m) || generic.some((g) => m.startsWith(g)));
      } catch {
        return false;
      }
    };

    const initialHarmonyFormat =
      storedHarmonyFormatRaw == null
        ? isHarmonyPreferred(storedTransformerModel, storedApiModel)
        : storedHarmonyFormat;

    // Ensure local history shape matches backend expectations: [{role, text}]
    let initialHistory = [];
    try {
      const parsed = storedHistory ? JSON.parse(storedHistory) : [];
      if (Array.isArray(parsed)) {
        initialHistory = parsed.filter(
          (h) =>
            h &&
            typeof h === "object" &&
            (h.role === "user" || h.role === "ai") &&
            typeof h.text === "string",
        );
      }
    } catch {
      initialHistory = [];
    }

    return {
      context: null, // Initial state for model context
      conversation: parseStoredConversation(storedConversation),
      history: initialHistory,
      sessionId: initialSessionId,
      sessionName: storedSessionName || defaultNameFromId(initialSessionId),
      approvalLevel: storedLevel,
      theme: storedTheme,
      visualTheme: storedVisualTheme,
      backendMode: storedBackendMode,
      apiModel: storedApiModel,
      apiModels: [],
      apiModelsUpdatedAt: null,
      registeredLocalModels: [],
      localModel: storedLocalModel,
      transformerModel: storedTransformerModel,
      staticModel: storedStaticModel,
      harmonyFormat: initialHarmonyFormat,
      harmonyTouched: storedHarmonyTouched,
      serverUrl: storedServerUrl,
      sttModel: storedSttModel,
      ttsModel: storedTtsModel,
      voiceModel: storedVoiceModel,
      liveTranscriptEnabled: storedLiveTranscriptEnabled,
      liveCameraDefaultEnabled: storedLiveCameraDefaultEnabled,
      workflowProfile: storedWorkflowProfile,
      captureRetentionDays: storedCaptureRetentionDays,
      captureDefaultSensitivity: storedCaptureDefaultSensitivity,
      captureAllowModelRawImageAccess: storedCaptureAllowModelRawImageAccess,
      captureAllowSummaryFallback: storedCaptureAllowSummaryFallback,
      enabledWorkflowModules: storedEnabledWorkflowModules,
      userTimezone: storedUserTimezone,
      preferredMicDeviceId: storedPreferredMicDeviceId,
      preferredCameraDeviceId: storedPreferredCameraDeviceId,
      micInputGain: Math.min(2, Math.max(0.25, storedMicInputGain)),
      outputVolume: Math.min(1.5, Math.max(0, storedOutputVolume)),
      visionModel: storedVisionModel,
      maxContextLength: storedMaxContextLength,
      kvCache: storedKvCache,
      ramSwap: storedRamSwap,
      devMode: false,
      apiStatus: "loading",
      apiProviderStatus: "unknown",
      wsStatus: "offline", // WebSocket status for thought stream
      wsLastEventAt: storedWsLastEventAt,
      wsLastError: storedWsLastError,
      wsLastErrorAt: storedWsLastErrorAt,
      devices: [],
      defaultDevice: null,
      inferenceDevice: null,
      cudaDiagnostics: null,
      calendarEvents: [],
      selectedCalendarDate: new Date(),
      requestTimeoutSec: storedRequestTimeoutSec ?? 30,
      streamIdleTimeoutSec: storedStreamIdleTimeoutSec ?? 120,
      thinkingMode: storedThinkingMode,
      toolDisplayMode: storedToolDisplayMode,
      toolLinkBehavior: storedToolLinkBehavior,
      customThemes: storedCustomThemes,
    };
  });
  const [userSettingsLoaded, setUserSettingsLoaded] = useState(false);
  const lastUserSettingsRef = useRef(null);
  const apiProbeStateRef = useRef({
    apiModelsUpdatedAt: null,
    apiProviderStatus: "unknown",
  });
  registerCustomThemes(Array.isArray(state.customThemes) ? state.customThemes : []);

  useEffect(() => {
    apiProbeStateRef.current = {
      apiModelsUpdatedAt: state.apiModelsUpdatedAt,
      apiProviderStatus: state.apiProviderStatus,
    };
  }, [state.apiModelsUpdatedAt, state.apiProviderStatus]);

  // Check API health and update status
  useEffect(() => {
    const updateApiState = (status, providerStatus, models) => {
      setState((prev) => {
        const prevProvider = prev.apiProviderStatus ?? "unknown";
        const nextProvider = providerStatus ?? "unknown";
        const hasModelsUpdate = Array.isArray(models);
        const nextModels = hasModelsUpdate ? models : prev.apiModels;
        const nextModelsUpdatedAt = hasModelsUpdate ? Date.now() : prev.apiModelsUpdatedAt;
        return {
          ...prev,
          apiStatus: status,
          apiProviderStatus: providerStatus,
          ...(hasModelsUpdate
            ? {
                apiModels: nextModels,
                apiModelsUpdatedAt: nextModelsUpdatedAt,
              }
            : {}),
        };
      });
    };

    const classifyProviderStatus = (error) => {
      if (!error || !error.response) return "offline";
      const status = error.response.status;
      const data = error.response.data;
      const detail =
        typeof data === "string"
          ? data
          : (data && (data.detail || data.message)) || "";
      const detailLower = String(detail).toLowerCase();
      if (status === 400 && detailLower.includes("api key")) return "unconfigured";
      if (status === 401 || status === 403) return "unauthorized";
      if (status === 404) return "unreachable";
      if (status >= 500) return "offline";
      return "error";
    };

    if (state.backendMode !== "api") {
      updateApiState("online", "bypassed");
      return;
    }

    let attempts = 0;
    let timeoutId;
    const maxAttempts = 5;
    const offlineRetryMs = 30000;
    const onlinePollMs = 5 * 60 * 1000;

    const checkApi = async () => {
      try {
        const res = await axios.get("/api/health");
        if (res.data && res.data.status === "healthy") {
          const probeState = apiProbeStateRef.current;
          let providerStatus = probeState.apiProviderStatus ?? "online";
          let apiModels = null;
          if (shouldRefreshProviderModels(probeState)) {
            try {
              const r = await axios.get("/api/openai/models");
              const models = r?.data?.models;
              providerStatus = "online";
              apiModels = Array.isArray(models) ? models : [];
            } catch (providerErr) {
              providerStatus = classifyProviderStatus(providerErr);
            }
          }
          updateApiState("online", providerStatus, apiModels);
          attempts = 0;
          timeoutId = setTimeout(checkApi, onlinePollMs);
          return;
        }
        throw new Error("API unhealthy");
      } catch (err) {
        attempts += 1;
        if (err && err.code === "ECONNREFUSED") {
          console.debug("API connection refused");
        }
        const delay = Math.min(1000 * 2 ** Math.max(attempts - 1, 0), 30000);
        if (attempts >= maxAttempts) {
          updateApiState("offline", "offline");
          attempts = 0; // reset so the next scheduled probe gets the full backoff window
          timeoutId = setTimeout(checkApi, offlineRetryMs);
          return;
        }
        updateApiState("loading", "unknown");
        timeoutId = setTimeout(checkApi, delay);
      }
    };

    checkApi();
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [state.backendMode, setState]);

  // Load persisted user settings from backend once API is ready
  useEffect(() => {
    if (!(state.apiStatus === "online" && state.backendMode === "api")) {
      setUserSettingsLoaded(false);
      lastUserSettingsRef.current = null;
      return;
    }
    Promise.all([axios.get("/api/themes"), axios.get("/api/user-settings")])
      .then(([themesRes, settingsRes]) => {
        const customThemes = Array.isArray(themesRes?.data?.themes)
          ? themesRes.data.themes
          : [];
        registerCustomThemes(customThemes);
        const data = settingsRes.data;
        setState((prev) => {
          const nextCustomThemes = customThemes;
          const nextApproval = data.approval_level || prev.approvalLevel;
          const nextTheme = data.theme || prev.theme;
          const nextVisualTheme = normalizeVisualTheme(
            typeof data.visual_theme === "string" ? data.visual_theme : prev.visualTheme,
          );
          const nextToolDisplay = normalizeToolDisplayMode(
            typeof data.tool_display_mode === "string"
              ? data.tool_display_mode
              : prev.toolDisplayMode,
          );
          const nextToolLink = normalizeToolLinkBehavior(
            typeof data.tool_link_behavior === "string"
              ? data.tool_link_behavior
              : prev.toolLinkBehavior,
          );
          const nextLiveTranscriptEnabled =
            typeof data.live_transcript_enabled === "boolean"
              ? data.live_transcript_enabled
              : prev.liveTranscriptEnabled;
          const nextLiveCameraDefaultEnabled =
            typeof data.live_camera_default_enabled === "boolean"
              ? data.live_camera_default_enabled
              : prev.liveCameraDefaultEnabled;
          const nextWorkflowProfile =
            typeof data.default_workflow === "string" && data.default_workflow.trim()
              ? data.default_workflow.trim()
              : prev.workflowProfile;
          const nextCaptureRetentionDays =
            typeof data.capture_retention_days === "number"
              ? data.capture_retention_days
              : prev.captureRetentionDays;
          const nextCaptureDefaultSensitivity =
            typeof data.capture_default_sensitivity === "string" &&
            data.capture_default_sensitivity.trim()
              ? data.capture_default_sensitivity.trim()
              : prev.captureDefaultSensitivity;
          const nextCaptureAllowRawImageAccess =
            typeof data.capture_allow_model_raw_image_access === "boolean"
              ? data.capture_allow_model_raw_image_access
              : prev.captureAllowModelRawImageAccess;
          const nextCaptureAllowSummaryFallback =
            typeof data.capture_allow_summary_fallback === "boolean"
              ? data.capture_allow_summary_fallback
              : prev.captureAllowSummaryFallback;
          const nextEnabledWorkflowModules = Array.isArray(
            data.enabled_workflow_modules,
          )
            ? data.enabled_workflow_modules
            : prev.enabledWorkflowModules;
          const nextUserTimezone =
            typeof data.user_timezone === "string"
              ? data.user_timezone
              : prev.userTimezone;
          const nextRegisteredLocalModels = Array.isArray(
            data.local_model_registrations,
          )
            ? data.local_model_registrations
            : prev.registeredLocalModels;
          lastUserSettingsRef.current = {
            approvalLevel: nextApproval,
            theme: nextTheme,
            visualTheme: nextVisualTheme,
            toolDisplayMode: nextToolDisplay,
            toolLinkBehavior: nextToolLink,
            liveTranscriptEnabled: nextLiveTranscriptEnabled,
            liveCameraDefaultEnabled: nextLiveCameraDefaultEnabled,
            workflowProfile: nextWorkflowProfile,
            captureRetentionDays: nextCaptureRetentionDays,
            captureDefaultSensitivity: nextCaptureDefaultSensitivity,
            captureAllowModelRawImageAccess: nextCaptureAllowRawImageAccess,
            captureAllowSummaryFallback: nextCaptureAllowSummaryFallback,
            enabledWorkflowModules: nextEnabledWorkflowModules,
            userTimezone: nextUserTimezone,
          };
          return {
            ...prev,
            // Do not overwrite transcript history; backend returns list of session IDs here.
            // If needed, wire this to a separate state key for a session picker.
            approvalLevel: nextApproval,
            theme: nextTheme,
            visualTheme: nextVisualTheme,
            customThemes: nextCustomThemes,
            toolDisplayMode: nextToolDisplay,
            toolLinkBehavior: nextToolLink,
            liveTranscriptEnabled: nextLiveTranscriptEnabled,
            liveCameraDefaultEnabled: nextLiveCameraDefaultEnabled,
            workflowProfile: nextWorkflowProfile,
            captureRetentionDays: nextCaptureRetentionDays,
            captureDefaultSensitivity: nextCaptureDefaultSensitivity,
            captureAllowModelRawImageAccess: nextCaptureAllowRawImageAccess,
            captureAllowSummaryFallback: nextCaptureAllowSummaryFallback,
            enabledWorkflowModules: nextEnabledWorkflowModules,
            userTimezone: nextUserTimezone,
            registeredLocalModels: nextRegisteredLocalModels,
          };
        });
        setUserSettingsLoaded(true);
      })
      .catch(() => {
        setUserSettingsLoaded(true);
      });
  }, [state.apiStatus, state.backendMode]);

  // Pre-register the service worker (push registration will still require a user gesture later)
  useEffect(() => {
    ensureServiceWorker();
  }, []);

  // Phase 1: auto-enroll device and fetch a token on startup when API is online
  useEffect(() => {
    if (state.apiStatus === "online" && state.backendMode === "api") {
      ensureDeviceAndToken().catch(() => {});
    }
  }, [state.apiStatus, state.backendMode]);

  useEffect(() => {
    localStorage.setItem("conversation", JSON.stringify(state.conversation));
  }, [state.conversation]);

  // Persist conversation to server storage
  useEffect(() => {
    if (
      state.apiStatus === "online" &&
      state.sessionId
    ) {
      if (typeof sessionStorage !== "undefined") {
        try {
          const key = `float:conv-loaded:${state.sessionId}`;
          const snapshot = sessionStorage.getItem(key);
          if (snapshot) {
            const current = JSON.stringify(state.conversation || []);
            if (snapshot === current) {
              sessionStorage.removeItem(key);
              return;
            }
          }
        } catch {}
      }
      axios
        .post(`/api/conversations/${state.sessionId}`, {
          name: state.sessionName,
          session_id: state.sessionId,
          messages: state.conversation,
        })
        .catch((err) => console.error("Failed to save conversation", err));
    }
  }, [
    state.conversation,
    state.sessionId,
    state.sessionName,
    state.apiStatus,
  ]);

  useEffect(() => {
    localStorage.setItem("history", JSON.stringify(state.history));
  }, [state.history]);

  useEffect(() => {
    if (state.sessionId) {
      localStorage.setItem("sessionId", state.sessionId);
    }
  }, [state.sessionId]);

  useEffect(() => {
    if (state.sessionName) {
      localStorage.setItem("sessionName", state.sessionName);
    }
  }, [state.sessionName]);

  useEffect(() => {
    localStorage.setItem("approvalLevel", state.approvalLevel);
  }, [state.approvalLevel]);

  useEffect(() => {
    localStorage.setItem("theme", state.theme);
    document.body.classList.toggle("dark-mode", state.theme === "dark");
  }, [state.theme]);

  useEffect(() => {
    localStorage.setItem("visualTheme", normalizeVisualTheme(state.visualTheme));
  }, [state.visualTheme]);

  useEffect(() => {
    registerCustomThemes(Array.isArray(state.customThemes) ? state.customThemes : []);
    localStorage.setItem(
      "customThemes",
      JSON.stringify(Array.isArray(state.customThemes) ? state.customThemes : []),
    );
  }, [state.customThemes]);

  useEffect(() => {
    if (state.toolDisplayMode) {
      localStorage.setItem("toolDisplayMode", state.toolDisplayMode);
    } else {
      localStorage.removeItem("toolDisplayMode");
    }
  }, [state.toolDisplayMode]);

  useEffect(() => {
    if (state.toolLinkBehavior) {
      localStorage.setItem("toolLinkBehavior", state.toolLinkBehavior);
    } else {
      localStorage.removeItem("toolLinkBehavior");
    }
  }, [state.toolLinkBehavior]);

  useEffect(() => {
    localStorage.setItem("backendMode", state.backendMode);
  }, [state.backendMode]);

  useEffect(() => {
    localStorage.setItem("apiModel", state.apiModel);
  }, [state.apiModel]);

  useEffect(() => {
    localStorage.setItem("localModel", state.localModel);
  }, [state.localModel]);

  useEffect(() => {
    localStorage.setItem("transformerModel", state.transformerModel);
  }, [state.transformerModel]);

  // Auto-default Harmony when model changes, unless user explicitly toggled
  useEffect(() => {
    const isHarmonyPreferred = (...models) => {
      try {
        const generic = [
          "gpt-4o",
          "gpt-4.1",
          "gpt-5-mini",
          "gpt-5",
          "gpt-5.1",
          "gpt-5.2",
        ];
        return models
          .filter(Boolean)
          .map((model) => String(model).toLowerCase())
          .some((m) => isGptOssModel(m) || generic.some((g) => m.startsWith(g)));
      } catch {
        return false;
      }
    };
    if (!state.harmonyTouched) {
      const preferred = isHarmonyPreferred(
        state.transformerModel,
        state.apiModel,
      );
      if (preferred !== state.harmonyFormat) {
        setState((prev) => ({ ...prev, harmonyFormat: preferred }));
      }
    }
  }, [
    state.transformerModel,
    state.apiModel,
    state.harmonyFormat,
    state.harmonyTouched,
  ]);

  useEffect(() => {
    localStorage.setItem("staticModel", state.staticModel);
  }, [state.staticModel]);

  useEffect(() => {
    localStorage.setItem("harmonyFormat", String(state.harmonyFormat));
  }, [state.harmonyFormat]);

  useEffect(() => {
    localStorage.setItem("harmonyTouched", String(state.harmonyTouched));
  }, [state.harmonyTouched]);

  useEffect(() => {
    localStorage.setItem("serverUrl", state.serverUrl);
  }, [state.serverUrl]);

  useEffect(() => {
    localStorage.setItem("sttModel", state.sttModel);
  }, [state.sttModel]);

  useEffect(() => {
    localStorage.setItem("ttsModel", state.ttsModel);
  }, [state.ttsModel]);

  useEffect(() => {
    localStorage.setItem("voiceModel", state.voiceModel);
  }, [state.voiceModel]);

  useEffect(() => {
    localStorage.setItem(
      "liveTranscriptEnabled",
      String(state.liveTranscriptEnabled !== false),
    );
  }, [state.liveTranscriptEnabled]);

  useEffect(() => {
    localStorage.setItem(
      "liveCameraDefaultEnabled",
      String(state.liveCameraDefaultEnabled === true),
    );
  }, [state.liveCameraDefaultEnabled]);

  useEffect(() => {
    localStorage.setItem("workflowProfile", String(state.workflowProfile || "default"));
  }, [state.workflowProfile]);

  useEffect(() => {
    localStorage.setItem(
      "captureRetentionDays",
      String(Math.max(0, Number(state.captureRetentionDays) || 0)),
    );
  }, [state.captureRetentionDays]);

  useEffect(() => {
    localStorage.setItem(
      "captureDefaultSensitivity",
      String(state.captureDefaultSensitivity || "personal"),
    );
  }, [state.captureDefaultSensitivity]);

  useEffect(() => {
    localStorage.setItem(
      "captureAllowModelRawImageAccess",
      String(state.captureAllowModelRawImageAccess !== false),
    );
  }, [state.captureAllowModelRawImageAccess]);

  useEffect(() => {
    localStorage.setItem(
      "captureAllowSummaryFallback",
      String(state.captureAllowSummaryFallback !== false),
    );
  }, [state.captureAllowSummaryFallback]);

  useEffect(() => {
    localStorage.setItem(
      "enabledWorkflowModules",
      JSON.stringify(
        Array.isArray(state.enabledWorkflowModules) ? state.enabledWorkflowModules : [],
      ),
    );
  }, [state.enabledWorkflowModules]);

  useEffect(() => {
    if (state.userTimezone) {
      localStorage.setItem("userTimezone", state.userTimezone);
    } else {
      localStorage.removeItem("userTimezone");
    }
  }, [state.userTimezone]);

  useEffect(() => {
    localStorage.setItem(
      "preferredMicDeviceId",
      String(state.preferredMicDeviceId || ""),
    );
  }, [state.preferredMicDeviceId]);

  useEffect(() => {
    localStorage.setItem(
      "preferredCameraDeviceId",
      String(state.preferredCameraDeviceId || ""),
    );
  }, [state.preferredCameraDeviceId]);

  useEffect(() => {
    localStorage.setItem(
      "micInputGain",
      String(
        Math.min(2, Math.max(0.25, Number(state.micInputGain) || 1)),
      ),
    );
  }, [state.micInputGain]);

  useEffect(() => {
    localStorage.setItem(
      "outputVolume",
      String(Math.min(1.5, Math.max(0, Number(state.outputVolume) || 1))),
    );
  }, [state.outputVolume]);

  useEffect(() => {
    localStorage.setItem("visionModel", state.visionModel);
  }, [state.visionModel]);

  useEffect(() => {
    localStorage.setItem("maxContextLength", String(state.maxContextLength));
  }, [state.maxContextLength]);

  useEffect(() => {
    localStorage.setItem("kvCache", String(state.kvCache));
  }, [state.kvCache]);

  useEffect(() => {
    localStorage.setItem("ramSwap", String(state.ramSwap));
  }, [state.ramSwap]);

  useEffect(() => {
    localStorage.setItem("thinkingMode", String(state.thinkingMode || "auto"));
  }, [state.thinkingMode]);

  useEffect(() => {
    if (state.wsLastEventAt != null) {
      localStorage.setItem("wsLastEventAt", String(state.wsLastEventAt));
    } else {
      localStorage.removeItem("wsLastEventAt");
    }
  }, [state.wsLastEventAt]);

  useEffect(() => {
    if (state.wsLastError) {
      localStorage.setItem("wsLastError", state.wsLastError);
    } else {
      localStorage.removeItem("wsLastError");
    }
  }, [state.wsLastError]);

  useEffect(() => {
    if (state.wsLastErrorAt != null) {
      localStorage.setItem("wsLastErrorAt", String(state.wsLastErrorAt));
    } else {
      localStorage.removeItem("wsLastErrorAt");
    }
  }, [state.wsLastErrorAt]);

  useEffect(() => {
    const value = state.requestTimeoutSec;
    if (Number.isFinite(value) && value > 0) {
      localStorage.setItem("requestTimeoutSec", String(value));
    } else {
      localStorage.removeItem("requestTimeoutSec");
    }
  }, [state.requestTimeoutSec]);

  useEffect(() => {
    const value = state.streamIdleTimeoutSec;
    if (Number.isFinite(value) && value > 0) {
      localStorage.setItem("streamIdleTimeoutSec", String(value));
    } else {
      localStorage.removeItem("streamIdleTimeoutSec");
    }
  }, [state.streamIdleTimeoutSec]);

  // Persist select user settings to backend with a small debounce to avoid spam
  useEffect(() => {
    if (!(state.apiStatus === "online" && state.backendMode === "api")) {
      return undefined;
    }
    if (!userSettingsLoaded) {
      return undefined;
    }

    const lastSent = lastUserSettingsRef.current;
    if (
      lastSent &&
      lastSent.approvalLevel === state.approvalLevel &&
      lastSent.theme === state.theme &&
      lastSent.visualTheme === state.visualTheme &&
      lastSent.toolDisplayMode === state.toolDisplayMode &&
      lastSent.toolLinkBehavior === state.toolLinkBehavior &&
      lastSent.liveTranscriptEnabled === state.liveTranscriptEnabled &&
      lastSent.liveCameraDefaultEnabled === state.liveCameraDefaultEnabled &&
      lastSent.workflowProfile === state.workflowProfile &&
      lastSent.captureRetentionDays === state.captureRetentionDays &&
      lastSent.captureDefaultSensitivity === state.captureDefaultSensitivity &&
      lastSent.captureAllowModelRawImageAccess === state.captureAllowModelRawImageAccess &&
      lastSent.captureAllowSummaryFallback === state.captureAllowSummaryFallback &&
      JSON.stringify(lastSent.enabledWorkflowModules || []) ===
        JSON.stringify(state.enabledWorkflowModules || []) &&
      lastSent.userTimezone === state.userTimezone
    ) {
      return undefined;
    }

    const timeoutId = setTimeout(() => {
      axios
        .post("/api/user-settings", {
          approval_level: state.approvalLevel,
          theme: state.theme,
          visual_theme: normalizeVisualTheme(state.visualTheme),
          tool_display_mode: state.toolDisplayMode,
          tool_link_behavior: state.toolLinkBehavior,
          live_transcript_enabled: state.liveTranscriptEnabled !== false,
          live_camera_default_enabled: state.liveCameraDefaultEnabled === true,
          capture_retention_days: Math.max(0, Number(state.captureRetentionDays) || 0),
          capture_default_sensitivity: state.captureDefaultSensitivity || "personal",
          capture_allow_model_raw_image_access:
            state.captureAllowModelRawImageAccess !== false,
          capture_allow_summary_fallback:
            state.captureAllowSummaryFallback !== false,
          default_workflow: state.workflowProfile || "default",
          enabled_workflow_modules: Array.isArray(state.enabledWorkflowModules)
            ? state.enabledWorkflowModules
            : [],
          user_timezone: state.userTimezone || "",
        })
        .then(() => {
          lastUserSettingsRef.current = {
            approvalLevel: state.approvalLevel,
            theme: state.theme,
            visualTheme: normalizeVisualTheme(state.visualTheme),
            toolDisplayMode: state.toolDisplayMode,
            toolLinkBehavior: state.toolLinkBehavior,
            liveTranscriptEnabled: state.liveTranscriptEnabled,
            liveCameraDefaultEnabled: state.liveCameraDefaultEnabled,
            workflowProfile: state.workflowProfile,
            captureRetentionDays: state.captureRetentionDays,
            captureDefaultSensitivity: state.captureDefaultSensitivity,
            captureAllowModelRawImageAccess: state.captureAllowModelRawImageAccess,
            captureAllowSummaryFallback: state.captureAllowSummaryFallback,
            enabledWorkflowModules: Array.isArray(state.enabledWorkflowModules)
              ? state.enabledWorkflowModules
              : [],
            userTimezone: state.userTimezone,
          };
        })
        .catch(() => {});
    }, 400);

    return () => clearTimeout(timeoutId);
  }, [
    state.approvalLevel,
    state.theme,
    state.visualTheme,
    state.toolDisplayMode,
    state.toolLinkBehavior,
    state.liveTranscriptEnabled,
    state.liveCameraDefaultEnabled,
    state.workflowProfile,
    state.captureRetentionDays,
    state.captureDefaultSensitivity,
    state.captureAllowModelRawImageAccess,
    state.captureAllowSummaryFallback,
    state.enabledWorkflowModules,
    state.userTimezone,
    state.apiStatus,
    state.backendMode,
    userSettingsLoaded,
  ]);

  const settingsSyncReady =
    state.backendMode !== "api" ||
    state.apiStatus === "online" ||
    state.apiStatus === "bypassed";

  useEffect(() => {
    let cancelled = false;
    if (!settingsSyncReady) {
      return () => {
        cancelled = true;
      };
    }
    axios
      .get("/api/settings")
      .then((res) => {
        if (cancelled) return;
        const data = res.data || {};
        setState((prev) => {
          const next = { ...prev };
          let changed = false;
          const runtimeSelection = applyBackendRuntimeSelection(prev, data);
          if (runtimeSelection !== prev) {
            Object.assign(next, runtimeSelection);
            changed = true;
          }
          if (typeof data.dev_mode !== "undefined" && Boolean(data.dev_mode) !== prev.devMode) {
            next.devMode = Boolean(data.dev_mode);
            changed = true;
          }
          const timeoutCandidate =
            data.request_timeout ??
            data.llm_request_timeout ??
            data.timeout;
          const timeoutSec = Number(timeoutCandidate);
          if (Number.isFinite(timeoutSec) && timeoutSec > 0 && timeoutSec !== prev.requestTimeoutSec) {
            next.requestTimeoutSec = timeoutSec;
            changed = true;
          }
          const streamIdleSec = Number(data.stream_idle_timeout);
          if (Number.isFinite(streamIdleSec) && streamIdleSec > 0 && streamIdleSec !== prev.streamIdleTimeoutSec) {
            next.streamIdleTimeoutSec = streamIdleSec;
            changed = true;
          }
          return changed ? next : prev;
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [settingsSyncReady, state.apiStatus]);

  const muiTheme = useMemo(
    () =>
      createTheme({
        palette: getMuiPaletteOptions(state.visualTheme, state.theme),
      }),
    [state.theme, state.visualTheme],
  );

  useLayoutEffect(() => {
    const root = document.documentElement;
    applyVisualTheme(root, state.visualTheme, state.theme);
  }, [state.theme, state.visualTheme]);

  return (
    <GlobalContext.Provider value={{ state, setState }}>
      <ThemeProvider theme={muiTheme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </GlobalContext.Provider>
  );
};

const rootElement = document.getElementById("root");

if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <GlobalProvider>
        <App />
      </GlobalProvider>
    </React.StrictMode>,
  );
}
