import React from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { GlobalContext } from "../main";
import "../styles/Sidebar.css";
import "../styles/ToolActions.css";
import "../styles/ToolPayload.css";
import ActionHistoryPanel from "./ActionHistoryPanel";
import BrowserSessionDialog from "./BrowserSessionDialog";
import ToolEditorModal from "./ToolEditorModal";
import ToolPayloadView, {
  extractComputerPayload,
  summarizeToolPayload,
} from "./ToolPayloadView";
import { formatLocalRuntimeLabel, isLocalRuntimeEntry } from "../utils/modelUtils";
import {
  buildToolContinuationSignature,
  hasMatchingToolContinuationSignature,
} from "../utils/toolContinuations";
import {
  handleUnifiedPress,
  supportsHoverInteractions,
} from "../utils/pointerInteractions";
import {
  normalizeToolDisplayMode,
  toolDisplayShowsConsole,
} from "../utils/toolDisplayModes";
import { mergeContinuationText } from "../utils/continuationText";

const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 520;
const SIDEBAR_VIEWPORT_GUTTER = 160;
const SIDEBAR_KEYBOARD_STEP = 20;
const SIDEBAR_KEYBOARD_STEP_FAST = 40;
const EMPTY_GLOBAL_STATE = Object.freeze({});
const NOOP_SET_STATE = () => {};
const CLIENT_RESOLUTION_TOOLS = new Set(["camera.capture"]);
const TOOL_TRUST_TIERS = {
  "computer.observe": 1,
  "camera.capture": 1,
  "capture.list": 1,
  "computer.session.start": 2,
  "computer.session.stop": 2,
  "computer.navigate": 2,
  "computer.act": 2,
  "computer.windows.list": 2,
  "computer.windows.focus": 2,
  "computer.app.launch": 2,
  "capture.promote": 3,
  "capture.delete": 3,
  "shell.exec": 3,
  "patch.apply": 3,
  "mcp.call": 3,
};

const buildToolOutcomeResult = (status, message, data = null, ok = null) => {
  const normalized = String(status || "").toLowerCase();
  const resolvedOk =
    typeof ok === "boolean" ? ok : normalized && !["error", "denied"].includes(normalized);
  return {
    status,
    ok: Boolean(resolvedOk),
    message: message ?? null,
    data,
  };
};

const fallbackResultForStatus = (toolStatus) => {
  const normalized = String(toolStatus || "").toLowerCase();
  if (normalized === "denied") {
    return buildToolOutcomeResult("denied", "Denied by user.");
  }
  if (normalized === "error") {
    return buildToolOutcomeResult("error", "Tool error.");
  }
  return undefined;
};

const shouldAutoApproveTool = (approvalLevel, toolName) => {
  const normalizedApproval = String(approvalLevel || "all").toLowerCase();
  if (normalizedApproval === "auto") return true;
  if (normalizedApproval === "high") {
    return Number(TOOL_TRUST_TIERS[toolName] || 4) <= 2;
  }
  return false;
};

const statusTone = (status) => {
  const key = (status || "idle").toLowerCase();
  switch (key) {
    case "active":
    case "running":
    case "streaming":
      return { label: "active", hue: "var(--color-mint-green)" };
    case "waiting":
    case "pending":
    case "queued":
      return { label: "pending", hue: "var(--color-lavender)" };
    case "error":
    case "failed":
      return { label: "error", hue: "var(--color-accent)" };
    case "paused":
    case "stopped":
      return { label: "paused", hue: "var(--color-text-muted)" };
    default:
      return { label: key, hue: "var(--color-text-muted)" };
  }
};

const formatTimestamp = (timestamp) => {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const formatReviewTimestamp = (timestamp) => {
  if (!timestamp) return "";
  const numeric = Number(timestamp);
  if (!Number.isFinite(numeric) || numeric <= 0) return "";
  const date = new Date(numeric * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const normalizePreviewText = (value) => {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
};

const truncatePreviewText = (value, maxLength = 160) => {
  const text = normalizePreviewText(value);
  if (!text) return "";
  if (text.length <= maxLength) return text;
  const clipped = text.slice(0, Math.max(0, maxLength - 3)).trimEnd();
  return `${clipped}...`;
};

const summarizePreviewValue = (value, toolName) =>
  normalizePreviewText(summarizeToolPayload(value, toolName));

const formatStreamLabel = (entry) => {
  if (!entry || typeof entry !== "object") return "streaming response";
  const names = Array.isArray(entry.stream_names)
    ? entry.stream_names.filter((name) => typeof name === "string" && name.trim())
    : [];
  if (names.length === 1) {
    return `tool call: ${names[0].trim()}`;
  }
  if (names.length === 2) {
    return `tool calls: ${names[0].trim()}, ${names[1].trim()}`;
  }
  if (names.length > 2) {
    return `tool calls: ${names[0].trim()}, ${names[1].trim()} +${names.length - 2}`;
  }
  if (typeof entry.content === "string" && entry.content.trim()) {
    return entry.content.trim();
  }
  if (typeof entry.stream_preview === "string" && entry.stream_preview.trim()) {
    return entry.stream_preview.trim();
  }
  return "streaming response";
};

const buildEntryPreview = (entry, bodyText) => {
  if (!entry || typeof entry !== "object") return null;
  const normalizedBody = normalizePreviewText(bodyText);
  let full = "";
  if (entry.type === "stream") {
    full = normalizePreviewText(formatStreamLabel(entry)) || "streaming response";
  } else if (entry.type === "tool") {
    if (typeof entry.result !== "undefined" && entry.result !== null) {
      const resultSummary = summarizePreviewValue(entry.result, entry.name);
      full = resultSummary ? `result: ${resultSummary}` : "result";
    } else if (entry.args && typeof entry.args === "object" && Object.keys(entry.args).length > 0) {
      const argsSummary = summarizePreviewValue(entry.args, entry.name);
      full = argsSummary ? `args: ${argsSummary}` : "args";
    } else if (entry.status) {
      full = `status: ${normalizePreviewText(entry.status)}`;
    } else if (normalizedBody && normalizedBody !== "...") {
      full = normalizedBody;
    } else {
      full = "tool update";
    }
  } else if (normalizedBody && normalizedBody !== "...") {
    full = normalizedBody;
  } else if (entry.status) {
    full = normalizePreviewText(entry.status);
  }

  if (!full) return null;
  return { full, short: truncatePreviewText(full) };
};

const formatModelSourceLabel = (mode, model) => {
  const safeMode = typeof mode === "string" ? mode.trim() : "";
  const safeModel = typeof model === "string" ? model.trim() : "";
  if (safeMode && safeModel) return `${safeMode}:${safeModel}`;
  if (safeModel) return safeModel;
  if (safeMode) return safeMode;
  return "";
};

const normalizeToolStatus = (status) =>
  typeof status === "string" ? status.trim().toLowerCase() : "";

const getToolResultStatus = (result) => {
  if (result === null || typeof result === "undefined") return "";
  let parsed = result;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return "";
    }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
  return normalizeToolStatus(parsed.status);
};

const getEffectiveToolStatus = (tool) => {
  if (!tool || typeof tool !== "object") return "";
  const status = normalizeToolStatus(tool.status);
  if (status && status !== "proposed" && status !== "pending") {
    return status;
  }
  return getToolResultStatus(tool.result) || status;
};

const getBrowserSessionToolContext = (entry) => {
  if (!entry || entry.type !== "tool") return null;
  const computer = extractComputerPayload(entry.result, entry.name);
  const sessionId =
    computer?.sessionId ||
    (typeof entry.args?.session_id === "string" ? entry.args.session_id.trim() : "");
  if (!sessionId) return null;
  const runtime =
    computer?.runtime ||
    (typeof entry.args?.runtime === "string" ? entry.args.runtime.trim() : "");
  const currentUrl =
    computer?.currentUrl ||
    (typeof entry.args?.url === "string" ? entry.args.url.trim() : "");
  return {
    ...computer,
    sessionId,
    runtime,
    currentUrl,
    entry,
    timestamp:
      typeof entry.timestamp === "number" && Number.isFinite(entry.timestamp)
        ? entry.timestamp
        : 0,
    chainId: entry.chain_id || entry.message_id || null,
    messageId: entry.message_id || entry.chain_id || null,
    toolName: entry.name || "",
  };
};

const isToolReadyForContinue = (tool) => {
  if (!tool || typeof tool !== "object") return false;
  const status = getEffectiveToolStatus(tool);
  if (!status || status === "proposed" || status === "pending") return false;
  const hasResult = typeof tool.result !== "undefined" && tool.result !== null;
  if (hasResult) return true;
  return status === "denied" || status === "error";
};

const buildToolContinuationBatch = (tools) => {
  const list = Array.isArray(tools) ? tools.filter(Boolean) : [];
  if (!list.length) return null;
  if (!list.every(isToolReadyForContinue)) return null;
  return list;
};

const mergeToolUpdate = (tools, update) => {
  const list = Array.isArray(tools) ? [...tools] : [];
  if (!update || typeof update !== "object") return list;
  const rawId = update.id || update.request_id || null;
  let idx = -1;
  if (rawId) {
    idx = list.findIndex((t) => {
      if (!t || typeof t !== "object") return false;
      const tId = t.id || t.request_id || null;
      return tId ? String(tId) === String(rawId) : false;
    });
  }
  if (idx === -1) {
    const sig = JSON.stringify({ name: update.name, args: update.args || {} });
    idx = list.findIndex((t) => {
      if (!t || typeof t !== "object") return false;
      return (
        JSON.stringify({ name: t?.name, args: t?.args || {} }) === sig
      );
    });
  }
  if (idx >= 0) {
    list[idx] = { ...list[idx], ...update };
  } else {
    list.push(update);
  }
  return list;
};

const responseLabelForAction = (action) => {
  if (action?.response_label) return String(action.response_label);
  if (action?.response_id) {
    const id = String(action.response_id);
    return `response ${id.slice(-8)}`;
  }
  return "outside chat";
};

const buildFallbackDiff = (item) => {
  const beforeText = item?.diff?.before_text || "";
  const afterText = item?.diff?.after_text || "";
  const parts = [];
  if (beforeText) parts.push(`--- before\n${beforeText}`);
  if (afterText) parts.push(`+++ after\n${afterText}`);
  return parts.join("\n\n") || "No textual diff available.";
};

const normalizeDocsFocusTarget = (value) => {
  if (value == null) return "";
  return String(value).replace(/\\/g, "/").trim();
};

const buildDocsHref = (item) => {
  if (!item || typeof item !== "object") return "";
  const section = String(item.section || "").trim().toLowerCase();
  const resourceType = String(item.resource_type || "").trim().toLowerCase();
  if (resourceType !== "file" && section !== "knowledge") return "";
  const focusTarget = normalizeDocsFocusTarget(
    item.resource_id || item.label || item.resource_key,
  );
  if (!focusTarget) return "";
  return `/knowledge?tab=documents&id=${encodeURIComponent(focusTarget)}`;
};

const groupActionsByResponse = (actions) => {
  const groups = new Map();
  (Array.isArray(actions) ? actions : []).forEach((action) => {
    if (!action || typeof action !== "object" || !action.id) return;
    const responseId = String(action.response_id || "").trim();
    if (!responseId) return;
    if (!groups.has(responseId)) {
      groups.set(responseId, {
        key: responseId,
        responseId,
        responseLabel: responseLabelForAction(action),
        conversationId: action.conversation_id || null,
        actions: [],
      });
    }
    groups.get(responseId).actions.push(action);
  });
  groups.forEach((group) => {
    group.actions.sort(
      (a, b) =>
        (Number(b?.created_at_ts || b?.timestamp) || 0) -
        (Number(a?.created_at_ts || a?.timestamp) || 0),
    );
  });
  return groups;
};

const MIN_CONTEXT_LENGTH = 256;
const CONTEXT_STEP = 512;

const resolveEventTimestamp = (value) => {
  if (!value) return null;
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isNaN(ms) ? null : ms;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1.1e12 ? value : value * 1000;
  }
  if (typeof value === "string") {
    const parsed = new Date(value);
    const ms = parsed.getTime();
    return Number.isNaN(ms) ? null : ms;
  }
  if (typeof value === "object") {
    if (value.dateTime) return resolveEventTimestamp(value.dateTime);
    if (value.date) return resolveEventTimestamp(`${value.date}T00:00:00`);
  }
  return null;
};

const AgentConsole = ({
  collapsed = false,
  onToggle,
  streamEnabled = true,
  onStreamToggle,
  agents = [],
  onSelectMessage,
  isCalendar = false,
  events = [],
  backendReady = true,
  loadingSnapshot = false,
  onRefreshCalendar,
  onRefreshAgents,
  focus = null,
  actions = [],
  syncReviews = { pending: [], recent: [] },
}) => {
  const navigate = useNavigate();
  const globalContext = React.useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const preferredTimezone = React.useMemo(() => {
    const preferred =
      typeof state.userTimezone === "string" ? state.userTimezone.trim() : "";
    return preferred || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  }, [state.userTimezone]);
  const toolDisplayMode = React.useMemo(
    () => normalizeToolDisplayMode(state?.toolDisplayMode),
    [state?.toolDisplayMode],
  );
  const showToolEntries = toolDisplayShowsConsole(toolDisplayMode);
  const [taskQuery, setTaskQuery] = React.useState("");
  const [toolEditorState, setToolEditorState] = React.useState(null); // tool | task editor state
  const [collapsedChains, setCollapsedChains] = React.useState({});
  const [collapsedAgents, setCollapsedAgents] = React.useState({});
  const [expandedAgents, setExpandedAgents] = React.useState({});
  const [hiddenAgents, setHiddenAgents] = React.useState({});
  const [actionHistoryCollapsed, setActionHistoryCollapsed] = React.useState(false);
  const [actionHistoryHidden, setActionHistoryHidden] = React.useState(false);
  const [runtimeStatus, setRuntimeStatus] = React.useState(null);
  const [runtimeLoading, setRuntimeLoading] = React.useState(false);
  const [runtimeError, setRuntimeError] = React.useState("");
  const [providerStatus, setProviderStatus] = React.useState(null);
  const [providerModels, setProviderModels] = React.useState([]);
  const [providerLogs, setProviderLogs] = React.useState([]);
  const [providerLogsCursor, setProviderLogsCursor] = React.useState(0);
  const [providerLogsOpen, setProviderLogsOpen] = React.useState(false);
  const [providerSelectedModel, setProviderSelectedModel] = React.useState("");
  const [providerContextDraft, setProviderContextDraft] = React.useState("");
  const [providerPendingAction, setProviderPendingAction] = React.useState("");
  const [providerActionError, setProviderActionError] = React.useState("");
  const [contextDraft, setContextDraft] = React.useState("");
  const [contextDirty, setContextDirty] = React.useState(false);
  const [contextSaving, setContextSaving] = React.useState(false);
  const [contextError, setContextError] = React.useState("");
  const [contextPopupOpen, setContextPopupOpen] = React.useState(false);
  const [contextEditing, setContextEditing] = React.useState(false);
  const [contextEstimateMb, setContextEstimateMb] = React.useState(null);
  const [contextEstimateLoading, setContextEstimateLoading] = React.useState(false);
  const [contextEstimateError, setContextEstimateError] = React.useState("");
  const [modelVerify, setModelVerify] = React.useState(null);
  const [modelVerifyError, setModelVerifyError] = React.useState("");
  const [loadPending, setLoadPending] = React.useState(false);
  const [loadError, setLoadError] = React.useState("");
  const [unloadPending, setUnloadPending] = React.useState(false);
  const [unloadError, setUnloadError] = React.useState("");
  const [resourceSnapshot, setResourceSnapshot] = React.useState([]);
  const [isResizing, setIsResizing] = React.useState(false);
  const [actionHistoryDetails, setActionHistoryDetails] = React.useState({});
  const [openActionHistoryKey, setOpenActionHistoryKey] = React.useState("");
  const [actionHistoryPendingKey, setActionHistoryPendingKey] = React.useState("");
  const [actionHistoryFeedback, setActionHistoryFeedback] = React.useState("");
  const [syncReviewPendingKey, setSyncReviewPendingKey] = React.useState("");
  const [syncReviewFeedback, setSyncReviewFeedback] = React.useState("");
  const [syncInboxCollapsed, setSyncInboxCollapsed] = React.useState(false);
  const [browserSessionPopup, setBrowserSessionPopup] = React.useState(null);
  const [browserPopupPendingAction, setBrowserPopupPendingAction] = React.useState("");
  const [browserPopupError, setBrowserPopupError] = React.useState("");
  const [browserNavigateDraft, setBrowserNavigateDraft] = React.useState("");
  const [browserTypeDraft, setBrowserTypeDraft] = React.useState("");
  const [browserKeyDraft, setBrowserKeyDraft] = React.useState("Enter");
  const providerActionPending = Boolean(providerPendingAction);
  const sidebarRef = React.useRef(null);
  const focusTokenRef = React.useRef(null);
  const lastScrollAtBottomRef = React.useRef(true);
  const scrollBodyRef = React.useRef(null);
  const contextSliderRef = React.useRef(null);
  const contextWrapRef = React.useRef(null);
  const contextInputRef = React.useRef(null);
  const contextDraggingRef = React.useRef(false);
  const contextEstimateTimerRef = React.useRef(null);
  const contextEstimateTokenRef = React.useRef(0);
  const composerOverlapRef = React.useRef(0);
  const overlapRafRef = React.useRef(null);
  const overlapTimerRef = React.useRef(null);
  const lastVerifyRef = React.useRef({ model: null, at: 0 });
  const syncInboxInteractedRef = React.useRef(false);
  const selectedLocalProvider = React.useMemo(() => {
    const currentLocal =
      typeof state.localModel === "string" ? state.localModel.trim().toLowerCase() : "";
    if (isLocalRuntimeEntry(currentLocal)) return currentLocal;
    const runtimeProvider =
      typeof runtimeStatus?.provider === "string"
        ? runtimeStatus.provider.trim().toLowerCase()
        : "";
    if (isLocalRuntimeEntry(runtimeProvider)) return runtimeProvider;
    return "";
  }, [state.localModel, runtimeStatus?.provider]);
  const usingProviderRuntime =
    state.backendMode === "local" && Boolean(selectedLocalProvider);
  const isLocalMode = (state.backendMode || "").toLowerCase() === "local";
  const clampSidebarWidth = React.useCallback((value, minWidth, maxWidth) => {
    if (!Number.isFinite(value)) return minWidth;
    return Math.min(maxWidth, Math.max(minWidth, value));
  }, []);

  const getSidebarBounds = React.useCallback(() => {
    const minWidth = SIDEBAR_MIN_WIDTH;
    const maxWidth = Math.max(
      minWidth,
      Math.min(SIDEBAR_MAX_WIDTH, window.innerWidth - SIDEBAR_VIEWPORT_GUTTER),
    );
    return { minWidth, maxWidth };
  }, []);

  const applySidebarWidth = React.useCallback(
    (width) => {
      const root = typeof document !== "undefined" ? document.documentElement : null;
      if (!root) return;
      root.style.setProperty("--sidebar-width-right", `${width}px`);
      try {
        localStorage.setItem("sidebarWidthRight", String(width));
      } catch {}
    },
    [],
  );

  const resetSidebarWidth = React.useCallback(() => {
    const root = typeof document !== "undefined" ? document.documentElement : null;
    if (!root) return;
    root.style.removeProperty("--sidebar-width-right");
    try {
      localStorage.removeItem("sidebarWidthRight");
    } catch {}
  }, []);

  const nudgeSidebarWidth = React.useCallback(
    (delta) => {
      const sidebar = sidebarRef.current;
      const { minWidth, maxWidth } = getSidebarBounds();
      const currentWidth = sidebar?.getBoundingClientRect().width || minWidth;
      const next = clampSidebarWidth(currentWidth + delta, minWidth, maxWidth);
      applySidebarWidth(next);
    },
    [applySidebarWidth, clampSidebarWidth, getSidebarBounds],
  );

  const handleResizeKeyDown = React.useCallback(
    (event) => {
      if (collapsed) return;
      if (event.key === "Home") {
        event.preventDefault();
        resetSidebarWidth();
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey ? SIDEBAR_KEYBOARD_STEP_FAST : SIDEBAR_KEYBOARD_STEP;
      const delta = event.key === "ArrowLeft" ? step : -step;
      nudgeSidebarWidth(delta);
    },
    [collapsed, nudgeSidebarWidth, resetSidebarWidth],
  );

  const startResize = React.useCallback(
    (event) => {
      if (collapsed) return;
      if (event.button !== 0 && event.pointerType !== "touch") return;
      event.preventDefault();
      const root = document.documentElement;
      const sidebar = sidebarRef.current;
      const startX = event.clientX;
      const startWidth = sidebar?.getBoundingClientRect().width || 0;
      const { minWidth, maxWidth } = getSidebarBounds();
      root.style.setProperty("cursor", "col-resize");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      setIsResizing(true);

      const onMove = (moveEvent) => {
        const delta = startX - moveEvent.clientX;
        const next = clampSidebarWidth(startWidth + delta, minWidth, maxWidth);
        applySidebarWidth(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        root.style.removeProperty("cursor");
        setIsResizing(false);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [applySidebarWidth, clampSidebarWidth, collapsed, getSidebarBounds],
  );
  const conversationById = React.useMemo(() => {
    const map = new Map();
    (state.conversation || []).forEach((msg) => {
      if (msg && msg.id) {
        map.set(msg.id, msg);
      }
    });
    return map;
  }, [state.conversation]);
  const actionHistoryByResponse = React.useMemo(
    () => groupActionsByResponse(actions),
    [actions],
  );
  const visibleToolResponseIds = React.useMemo(() => {
    const ids = new Set();
    if (!showToolEntries) return ids;
    agents.forEach((agent) => {
      (Array.isArray(agent?.events) ? agent.events : []).forEach((entry) => {
        if (!entry || entry.type !== "tool") return;
        const responseId = String(entry.message_id || entry.chain_id || "").trim();
        if (responseId) ids.add(responseId);
      });
    });
    return ids;
  }, [agents, showToolEntries]);
  const hasContextualActionHistory = React.useMemo(() => {
    for (const responseId of actionHistoryByResponse.keys()) {
      if (visibleToolResponseIds.has(responseId)) return true;
    }
    return false;
  }, [actionHistoryByResponse, visibleToolResponseIds]);
  const showStandaloneActionHistory =
    Array.isArray(actions) &&
    actions.length > 0 &&
    (!showToolEntries || !hasContextualActionHistory);
  const pendingSyncReviews = React.useMemo(
    () => (Array.isArray(syncReviews?.pending) ? syncReviews.pending : []),
    [syncReviews],
  );
  const recentSyncReviews = React.useMemo(
    () => (Array.isArray(syncReviews?.recent) ? syncReviews.recent : []),
    [syncReviews],
  );
  const showSyncInbox =
    pendingSyncReviews.length > 0 || recentSyncReviews.length > 0;
  const toolContinueLocksRef = React.useRef(new Set());
  const autoToolResolveLocksRef = React.useRef(new Set());

  const formatBytes = React.useCallback((value) => {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
      return "n/a";
    }
    const gb = value / 1024 / 1024 / 1024;
    return `${gb.toFixed(gb < 10 ? 2 : 1)} GB`;
  }, []);

  const formatTokenCount = React.useCallback((value) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
    return `${Math.round(value)}`;
  }, []);
  const parseContextLength = React.useCallback((value) => {
    if (typeof value === "number" && Number.isFinite(value)) {
      return Math.round(value);
    }
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return null;
      const parsed = Number.parseInt(trimmed, 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    }
    return null;
  }, []);
  const snapContextLength = React.useCallback((value) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    if (value <= MIN_CONTEXT_LENGTH) return MIN_CONTEXT_LENGTH;
    const snapped = Math.round(value / CONTEXT_STEP) * CONTEXT_STEP;
    return Math.max(MIN_CONTEXT_LENGTH, snapped);
  }, []);
  const formatEstimate = React.useCallback(
    (valueMb) => {
      if (typeof valueMb !== "number" || !Number.isFinite(valueMb) || valueMb <= 0) {
        return "n/a";
      }
      return formatBytes(valueMb * 1024 * 1024);
    },
    [formatBytes],
  );

  React.useEffect(() => {
    if (contextDirty) return;
    const current = parseContextLength(state.maxContextLength);
    setContextDraft(current ? String(current) : "");
  }, [contextDirty, parseContextLength, state.maxContextLength]);

  const sliderRange = React.useMemo(() => {
    const current = parseContextLength(state.maxContextLength) ?? MIN_CONTEXT_LENGTH;
    const draftParsed = parseContextLength(contextDraft);
    const value = snapContextLength(draftParsed ?? current) ?? MIN_CONTEXT_LENGTH;
    const rawMax = Math.max(MIN_CONTEXT_LENGTH * 2, current, value);
    const max = Math.ceil(rawMax / CONTEXT_STEP) * CONTEXT_STEP;
    return {
      min: MIN_CONTEXT_LENGTH,
      max,
      value,
    };
  }, [
    contextDraft,
    parseContextLength,
    snapContextLength,
    state.maxContextLength,
  ]);

  const nudgeContextLength = React.useCallback(
    (delta) => {
      const base =
        snapContextLength(parseContextLength(contextDraft)) ??
        snapContextLength(parseContextLength(state.maxContextLength)) ??
        MIN_CONTEXT_LENGTH;
      const next = snapContextLength(base + delta) ?? MIN_CONTEXT_LENGTH;
      setContextDraft(String(next));
      setContextDirty(true);
      setContextError("");
    },
    [contextDraft, parseContextLength, snapContextLength, state.maxContextLength],
  );

  const applyContextLength = React.useCallback(async () => {
    const parsed = parseContextLength(contextDraft);
    const snapped = parsed ? snapContextLength(parsed) : null;
    if (!snapped || snapped <= 0) {
      setContextError("Context length must be a positive number.");
      return;
    }
    if (String(snapped) !== contextDraft) {
      setContextDraft(String(snapped));
    }
    setContextSaving(true);
    setContextError("");
    try {
      await axios.post("/api/settings", { max_context_length: snapped });
      setState((prev) =>
        prev.maxContextLength === snapped
          ? prev
          : { ...prev, maxContextLength: snapped },
      );
      setContextDirty(false);
    } catch (err) {
      setContextError("Unable to update context length.");
    } finally {
      setContextSaving(false);
    }
  }, [contextDraft, parseContextLength, setState, snapContextLength]);

  const updateContextFromPointer = React.useCallback(
    (clientX) => {
      const track = contextSliderRef.current;
      if (!track || typeof track.getBoundingClientRect !== "function") return;
      const rect = track.getBoundingClientRect();
      if (!rect.width) return;
      const ratio = (clientX - rect.left) / rect.width;
      const clamped = Math.min(1, Math.max(0, ratio));
      const rawValue =
        sliderRange.min + clamped * (sliderRange.max - sliderRange.min);
      const snapped = snapContextLength(rawValue);
      if (!snapped) return;
      setContextDraft(String(snapped));
      setContextDirty(true);
      setContextError("");
    },
    [sliderRange.max, sliderRange.min, snapContextLength],
  );

  const handleContextPointerDown = React.useCallback(
    (event) => {
      if (!backendReady) return;
      event.preventDefault();
      setContextPopupOpen(true);
      setContextEditing(false);
      updateContextFromPointer(event.clientX);
      contextDraggingRef.current = true;
      if (event.currentTarget?.setPointerCapture) {
        event.currentTarget.setPointerCapture(event.pointerId);
      }
    },
    [backendReady, updateContextFromPointer],
  );

  const handleContextPointerMove = React.useCallback(
    (event) => {
      if (!contextDraggingRef.current) return;
      updateContextFromPointer(event.clientX);
    },
    [updateContextFromPointer],
  );

  const handleContextPointerUp = React.useCallback((event) => {
    contextDraggingRef.current = false;
    if (event.currentTarget?.releasePointerCapture) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const handleContextKeyDown = React.useCallback(
    (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowUp") {
        event.preventDefault();
        setContextPopupOpen(true);
        nudgeContextLength(CONTEXT_STEP);
      } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
        event.preventDefault();
        setContextPopupOpen(true);
        nudgeContextLength(-CONTEXT_STEP);
      } else if (event.key === "Enter") {
        event.preventDefault();
        setContextPopupOpen(true);
        setContextEditing(true);
      }
    },
    [nudgeContextLength],
  );

  React.useEffect(() => {
    if (!contextPopupOpen) return undefined;
    const handleOutside = (event) => {
      if (contextWrapRef.current?.contains(event.target)) return;
      setContextPopupOpen(false);
      setContextEditing(false);
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [contextPopupOpen]);

  React.useEffect(() => {
    if (!contextEditing) return;
    if (contextInputRef.current) {
      contextInputRef.current.focus();
      contextInputRef.current.select();
    }
  }, [contextEditing]);

  React.useEffect(() => {
    if (!backendReady || state.backendMode !== "local") {
      setContextEstimateMb(null);
      setContextEstimateError("");
      setContextEstimateLoading(false);
      return undefined;
    }
    const target = sliderRange.value;
    if (!target) {
      setContextEstimateMb(null);
      setContextEstimateError("");
      setContextEstimateLoading(false);
      return undefined;
    }
    if (contextEstimateTimerRef.current) {
      clearTimeout(contextEstimateTimerRef.current);
    }
    const token = contextEstimateTokenRef.current + 1;
    contextEstimateTokenRef.current = token;
    setContextEstimateLoading(true);
    contextEstimateTimerRef.current = setTimeout(() => {
      axios
        .get("/api/vram-estimate", { params: { context_length: target } })
        .then((res) => {
          if (contextEstimateTokenRef.current !== token) return;
          const estimate = Number(res?.data?.estimate_mb);
          setContextEstimateMb(Number.isFinite(estimate) ? estimate : null);
          setContextEstimateError("");
        })
        .catch(() => {
          if (contextEstimateTokenRef.current !== token) return;
          setContextEstimateMb(null);
          setContextEstimateError("Unable to estimate VRAM.");
        })
        .finally(() => {
          if (contextEstimateTokenRef.current !== token) return;
          setContextEstimateLoading(false);
        });
    }, 240);
    return () => {
      if (contextEstimateTimerRef.current) {
        clearTimeout(contextEstimateTimerRef.current);
        contextEstimateTimerRef.current = null;
      }
    };
  }, [backendReady, sliderRange.value, state.backendMode]);

  const updateComposerOverlap = React.useCallback(() => {
    if (overlapRafRef.current) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(overlapRafRef.current);
      }
      overlapRafRef.current = null;
    }
    if (overlapTimerRef.current) {
      clearTimeout(overlapTimerRef.current);
      overlapTimerRef.current = null;
    }

    const runner = (fn) => {
      if (typeof requestAnimationFrame === "function") {
        overlapRafRef.current = requestAnimationFrame(fn);
        return;
      }
      overlapTimerRef.current = setTimeout(fn, 0);
    };

    runner(() => {
      overlapRafRef.current = null;
      overlapTimerRef.current = null;
      const scrollBody = scrollBodyRef.current;
      if (!scrollBody || typeof scrollBody.getBoundingClientRect !== "function") return;
      const previousDistanceFromBottom = Math.max(
        0,
        scrollBody.scrollHeight - scrollBody.clientHeight - scrollBody.scrollTop,
      );

      const composer =
        typeof document !== "undefined"
          ? document.querySelector(".input-box") || document.querySelector(".open-entry-btn")
          : null;

      if (!composer || typeof composer.getBoundingClientRect !== "function") {
        if (composerOverlapRef.current !== 0) {
          composerOverlapRef.current = 0;
          scrollBody.style.setProperty("--composer-overlap", "0px");
        }
        return;
      }

      const bodyRect = scrollBody.getBoundingClientRect();
      const composerRect = composer.getBoundingClientRect();
      const overlapX =
        Math.min(bodyRect.right, composerRect.right) -
        Math.max(bodyRect.left, composerRect.left);
      const overlapY =
        Math.min(bodyRect.bottom, composerRect.bottom) -
        Math.max(bodyRect.top, composerRect.top);
      const overlapPx =
        overlapX > 0 && overlapY > 0 ? Math.max(0, Math.ceil(overlapY)) : 0;

      if (composerOverlapRef.current === overlapPx) return;
      composerOverlapRef.current = overlapPx;
      scrollBody.style.setProperty("--composer-overlap", `${overlapPx}px`);

      if (lastScrollAtBottomRef.current) {
        if (typeof scrollBody.scrollTo === "function") {
          scrollBody.scrollTo({ top: scrollBody.scrollHeight, behavior: "auto" });
        } else {
          scrollBody.scrollTop = scrollBody.scrollHeight;
        }
        return;
      }
      scrollBody.scrollTop = Math.max(
        0,
        scrollBody.scrollHeight - scrollBody.clientHeight - previousDistanceFromBottom,
      );
    });
  }, []);

  const persistHistory = React.useCallback(async (sessionId, history) => {
    try {
      localStorage.setItem("history", JSON.stringify(history));
      const payload = JSON.stringify({ sessionId, history });
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon("/api/history", blob);
      } else {
        axios.post("/api/history", { sessionId, history }).catch(() => {});
      }
    } catch {}
  }, []);

  const fetchRuntimeStatus = React.useCallback(async () => {
    if (!backendReady || !isLocalMode) {
      setRuntimeStatus(null);
      setRuntimeError("");
      setRuntimeLoading(false);
      return;
    }
    setRuntimeLoading(true);
    setRuntimeError("");
    try {
      const res = await axios.get("/api/llm/local-status", {
        params: { quick: true },
        timeout: 2500,
      });
      setRuntimeStatus(res?.data?.runtime || null);
    } catch (err) {
      setRuntimeError("Unable to load runtime status.");
    } finally {
      setRuntimeLoading(false);
    }
  }, [backendReady, isLocalMode]);

  const fetchProviderStatus = React.useCallback(async () => {
    if (!backendReady || !selectedLocalProvider) return;
    try {
      const res = await axios.get("/api/llm/provider/status", {
        params: { provider: selectedLocalProvider, quick: true },
        timeout: 2500,
      });
      const runtime = res?.data?.runtime || null;
      setProviderStatus(runtime);
      const loadedModel = runtime?.loaded_model;
      if (typeof loadedModel === "string" && loadedModel.trim()) {
        setProviderSelectedModel((prev) =>
          prev && prev.trim() ? prev : loadedModel.trim(),
        );
      }
      if (runtime?.context_length) {
        setProviderContextDraft(String(runtime.context_length));
      }
    } catch (err) {
      setProviderStatus((prev) => prev);
    }
  }, [backendReady, selectedLocalProvider]);

  const fetchProviderModels = React.useCallback(async () => {
    if (!backendReady || !selectedLocalProvider) return;
    try {
      const res = await axios.get("/api/llm/provider/models", {
        params: { provider: selectedLocalProvider },
      });
      const models = Array.isArray(res?.data?.models) ? res.data.models : [];
      setProviderModels(models);
      setProviderSelectedModel((prev) => {
        if (prev && prev.trim()) return prev;
        const loaded =
          typeof res?.data?.runtime?.loaded_model === "string"
            ? res.data.runtime.loaded_model.trim()
            : "";
        if (loaded) return loaded;
        return models.length ? String(models[0]) : "";
      });
    } catch (err) {
      setProviderModels((prev) => prev);
    }
  }, [backendReady, selectedLocalProvider]);

  const fetchProviderLogs = React.useCallback(
    async ({ reset = false } = {}) => {
      if (!backendReady || !selectedLocalProvider) return;
      const cursor = reset ? 0 : providerLogsCursor;
      try {
        const res = await axios.get("/api/llm/provider/logs", {
          params: {
            provider: selectedLocalProvider,
            cursor,
            limit: 200,
          },
        });
        const logsPayload = res?.data?.logs || {};
        const entries = Array.isArray(logsPayload.entries) ? logsPayload.entries : [];
        const nextCursor = Number(logsPayload.next_cursor || cursor);
        setProviderLogsCursor(Number.isFinite(nextCursor) ? nextCursor : cursor);
        setProviderLogs((prev) => {
          const merged = reset ? entries : [...prev, ...entries];
          return merged.slice(-500);
        });
      } catch (err) {
        if (reset) {
          setProviderLogs([]);
          setProviderLogsCursor(0);
        }
      }
    },
    [backendReady, providerLogsCursor, selectedLocalProvider],
  );

  const runProviderAction = React.useCallback(
    async (endpoint, body = {}, actionName = "action") => {
      if (!backendReady || !selectedLocalProvider) return;
      setProviderPendingAction(actionName);
      setProviderActionError("");
      try {
        await axios.post(endpoint, { provider: selectedLocalProvider, ...body });
        await fetchRuntimeStatus();
        await fetchProviderStatus();
        await fetchProviderModels();
        await fetchProviderLogs();
      } catch (err) {
        const detail =
          err?.response?.data?.detail || "Provider action failed. Check runtime logs.";
        setProviderActionError(String(detail));
      } finally {
        setProviderPendingAction("");
      }
    },
    [
      backendReady,
      fetchProviderLogs,
      fetchProviderModels,
      fetchProviderStatus,
      fetchRuntimeStatus,
      selectedLocalProvider,
    ],
  );

  const handleProviderStart = React.useCallback(() => {
    runProviderAction("/api/llm/provider/start", {}, "start");
  }, [runProviderAction]);

  const handleProviderStop = React.useCallback(() => {
    runProviderAction("/api/llm/provider/stop", {}, "stop");
  }, [runProviderAction]);

  const handleProviderLoad = React.useCallback(() => {
    const contextLength = parseInt(providerContextDraft || "", 10);
    runProviderAction("/api/llm/provider/load", {
      model: providerSelectedModel || undefined,
      context_length:
        Number.isFinite(contextLength) && contextLength > 0
          ? contextLength
          : undefined,
    }, "load");
  }, [providerContextDraft, providerSelectedModel, runProviderAction]);

  const handleProviderUnload = React.useCallback(() => {
    runProviderAction("/api/llm/provider/unload", {
      model: providerSelectedModel || undefined,
    }, "unload");
  }, [providerSelectedModel, runProviderAction]);

  const fetchModelVerify = React.useCallback(
    async (modelName, { force = false } = {}) => {
      if (!backendReady || !modelName) return;
      const now = Date.now();
      if (
        !force &&
        lastVerifyRef.current.model === modelName &&
        now - lastVerifyRef.current.at < 60000
      ) {
        return;
      }
      lastVerifyRef.current = { model: modelName, at: now };
      setModelVerifyError("");
      try {
        const res = await axios.get(
          `/api/models/verify/${encodeURIComponent(modelName)}`,
        );
        setModelVerify(res?.data || null);
      } catch (err) {
        setModelVerifyError("Unable to verify local model files.");
      }
    },
    [backendReady],
  );

  const fetchResourceSnapshot = React.useCallback(async () => {
    if (!backendReady) return;
    try {
      const res = await axios.get("/api/agents/resources");
      setResourceSnapshot(res?.data?.resources || []);
    } catch (err) {
      setResourceSnapshot((prev) => prev);
    }
  }, [backendReady]);

  const handleUnloadLocalModel = React.useCallback(async () => {
    if (!backendReady) return;
    setUnloadPending(true);
    setUnloadError("");
    try {
      await axios.post("/api/llm/unload-local");
      await fetchRuntimeStatus();
    } catch (err) {
      setUnloadError("Unable to unload local model.");
    } finally {
      setUnloadPending(false);
    }
  }, [backendReady, fetchRuntimeStatus]);

  const handleLoadLocalModel = React.useCallback(async () => {
    if (!backendReady) return;
    setLoadPending(true);
    setLoadError("");
    try {
      await axios.post("/api/llm/load-local");
      await fetchRuntimeStatus();
    } catch (err) {
      setLoadError("Unable to load local model.");
    } finally {
      setLoadPending(false);
    }
  }, [backendReady, fetchRuntimeStatus]);

  const applyContinuation = React.useCallback(
    (assistantId, continuation, md) => {
      if (!assistantId || !continuation) return;
      setState((prev) => {
        const updatedConversation = Array.isArray(prev.conversation)
          ? [...prev.conversation]
          : [];
        const mIdx = updatedConversation.findIndex((m) => m && m.id === assistantId);
        if (mIdx !== -1) {
          const existingText = updatedConversation[mIdx]?.text || "";
          const joined = mergeContinuationText(
            existingText,
            continuation,
            updatedConversation[mIdx]?.metadata,
          );
          updatedConversation[mIdx] = {
            ...updatedConversation[mIdx],
            text: joined,
            timestamp: new Date().toISOString(),
            metadata: {
              ...(updatedConversation[mIdx]?.metadata || {}),
              ...(md || {}),
              tool_continued: true,
            },
          };
        }
        const hist = Array.isArray(prev.history) ? [...prev.history] : [];
        if (hist.length && hist[hist.length - 1].role === "ai") {
          const last = hist[hist.length - 1].text || "";
          hist[hist.length - 1] = {
            role: "ai",
            text: mergeContinuationText(last, continuation),
          };
        } else {
          hist.push({ role: "ai", text: continuation });
        }
        persistHistory(prev.sessionId, hist);
        return { ...prev, conversation: updatedConversation, history: hist };
      });
    },
    [persistHistory, setState],
  );

  const normalizedFocus = React.useMemo(() => {
    if (!focus) return null;
    const toStringOrNull = (value) =>
      value === null || value === undefined || value === ""
        ? null
        : String(value);
    return {
      ...focus,
      chainId: toStringOrNull(focus.chainId ?? focus.messageId ?? focus.message_id),
      toolId: toStringOrNull(focus.toolId ?? focus.tool_id),
      agentId: toStringOrNull(focus.agentId ?? focus.agent_id),
      ts: typeof focus.ts === "number" ? focus.ts : null,
    };
  }, [focus]);

  const handleApprovalLevelChange = React.useCallback(
    (event) => {
      const rawValue = event?.target?.value;
      const allowed = ["all", "high", "auto"];
      const next = allowed.includes(rawValue) ? rawValue : "all";
      setState((prev) =>
        prev.approvalLevel === next ? prev : { ...prev, approvalLevel: next },
      );
    },
    [setState],
  );

  const escapeSelector = (value) => {
    const str = String(value);
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
      return CSS.escape(str);
    }
    return str.replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
  };

  const matchesFocus = React.useCallback(
    (entry) => {
      if (!normalizedFocus) return false;
      if (!entry || typeof entry !== "object") return false;
      const entryTool = entry.id ?? entry.request_id ?? null;
      const entryChain = entry.chain_id ?? entry.message_id ?? entry.session_id ?? null;
      const entryAgent = entry.agent_id ?? entry.chain_id ?? entry.session_id ?? null;
      if (normalizedFocus.toolId && entryTool) {
        if (String(entryTool) === normalizedFocus.toolId) return true;
      }
      if (normalizedFocus.chainId && entryChain) {
        if (String(entryChain) === normalizedFocus.chainId) return true;
      }
      if (normalizedFocus.agentId && entryAgent) {
        if (String(entryAgent) === normalizedFocus.agentId) return true;
      }
      return false;
    },
    [normalizedFocus],
  );

  React.useEffect(() => {
    if (!normalizedFocus) {
      focusTokenRef.current = null;
      return;
    }
    const key = `${normalizedFocus.chainId || ""}:${normalizedFocus.toolId || ""}:${normalizedFocus.ts}`;
    if (focusTokenRef.current === key) return;
    focusTokenRef.current = key;
    const selectors = [];
    if (normalizedFocus.toolId) {
      selectors.push(`[data-tool-id="${escapeSelector(normalizedFocus.toolId)}"]`);
    }
    if (normalizedFocus.chainId) {
      selectors.push(`[data-chain-id="${escapeSelector(normalizedFocus.chainId)}"]`);
    }
    if (normalizedFocus.agentId) {
      selectors.push(`[data-agent-id="${escapeSelector(normalizedFocus.agentId)}"]`);
    }
    if (!selectors.length) return;
    const root = sidebarRef.current;
    if (!root || typeof root.querySelector !== "function") return;
    const target = root.querySelector(selectors.join(", "));
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [normalizedFocus]);

  React.useEffect(() => {
    const root = scrollBodyRef.current || sidebarRef.current;
    if (!root) return;
    if (!lastScrollAtBottomRef.current) return;
    if (typeof root.scrollTo === "function") {
      root.scrollTo({ top: root.scrollHeight, behavior: "auto" });
    } else {
      root.scrollTop = root.scrollHeight;
    }
  }, [agents]);

  React.useEffect(() => {
    const root = scrollBodyRef.current || sidebarRef.current;
    if (!root) return;
    const onScroll = () => {
      const distanceFromBottom = root.scrollHeight - root.clientHeight - root.scrollTop;
      lastScrollAtBottomRef.current = distanceFromBottom < 24;
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => root.removeEventListener("scroll", onScroll);
  }, []);

  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;
    updateComposerOverlap();
    const handleResize = () => updateComposerOverlap();
    window.addEventListener("resize", handleResize);

    let observer = null;
    let domObserver = null;
    const observeTargets = () => {
      if (!observer) return;
      observer.disconnect();
      if (scrollBodyRef.current) observer.observe(scrollBodyRef.current);
      const composer =
        typeof document !== "undefined"
          ? document.querySelector(".input-box") || document.querySelector(".open-entry-btn")
          : null;
      if (composer) observer.observe(composer);
    };
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => updateComposerOverlap());
      observeTargets();
    }
    if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
      domObserver = new MutationObserver((mutations) => {
        const hasComposerChange = mutations.some((mutation) =>
          Array.from(mutation.addedNodes || [])
            .concat(Array.from(mutation.removedNodes || []))
            .some((node) => {
              if (!(node instanceof Element)) return false;
              return (
                node.matches(".input-box, .open-entry-btn") ||
                node.querySelector(".input-box, .open-entry-btn")
              );
            }),
        );
        if (!hasComposerChange) return;
        observeTargets();
        updateComposerOverlap();
      });
      domObserver.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      window.removeEventListener("resize", handleResize);
      if (observer) observer.disconnect();
      if (domObserver) domObserver.disconnect();
      if (overlapRafRef.current) {
        if (typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(overlapRafRef.current);
        }
        overlapRafRef.current = null;
      }
      if (overlapTimerRef.current) {
        clearTimeout(overlapTimerRef.current);
        overlapTimerRef.current = null;
      }
    };
  }, [updateComposerOverlap, collapsed]);

  React.useEffect(() => {
    if (!backendReady || collapsed) return;
    let runtimeId = null;
    if (isLocalMode) {
      fetchRuntimeStatus();
      if (usingProviderRuntime && !providerActionPending) {
        fetchProviderStatus();
        fetchProviderModels();
        fetchProviderLogs({ reset: true });
      }
      runtimeId = setInterval(() => {
        fetchRuntimeStatus();
        if (usingProviderRuntime && !providerActionPending) {
          fetchProviderStatus();
          fetchProviderModels();
          fetchProviderLogs();
        }
      }, 8000);
    } else {
      setRuntimeStatus(null);
      setRuntimeError("");
      setProviderStatus(null);
      setProviderActionError("");
      setProviderModels([]);
      setProviderLogs([]);
      setProviderLogsCursor(0);
      setProviderPendingAction("");
    }
    fetchResourceSnapshot();
    const resourceId = setInterval(fetchResourceSnapshot, 12000);
    return () => {
      if (runtimeId) clearInterval(runtimeId);
      clearInterval(resourceId);
    };
  }, [
    backendReady,
    collapsed,
    fetchProviderLogs,
    fetchProviderModels,
    fetchProviderStatus,
    fetchResourceSnapshot,
    fetchRuntimeStatus,
    isLocalMode,
    providerActionPending,
    usingProviderRuntime,
  ]);

  React.useEffect(() => {
    const modelName =
      runtimeStatus?.model || state.transformerModel || state.localModel;
    if (!modelName) return;
    if (isLocalRuntimeEntry(modelName)) return;
    fetchModelVerify(modelName);
  }, [
    fetchModelVerify,
    runtimeStatus?.model,
    state.transformerModel,
    state.localModel,
  ]);

  React.useEffect(() => {
    setProviderActionError("");
    setProviderLogs([]);
    setProviderLogsCursor(0);
    setProviderStatus(null);
    setProviderModels([]);
    setProviderSelectedModel("");
    if (!usingProviderRuntime) return;
    fetchProviderStatus();
    fetchProviderModels();
    fetchProviderLogs({ reset: true });
  }, [
    fetchProviderLogs,
    fetchProviderModels,
    fetchProviderStatus,
    selectedLocalProvider,
    usingProviderRuntime,
  ]);

  const refreshDisabled = !backendReady || (!isCalendar && loadingSnapshot);
  const hiddenCount =
    Object.values(hiddenAgents).filter(Boolean).length +
    (showStandaloneActionHistory && actionHistoryHidden ? 1 : 0);
  const hasInlineToolActivity = React.useMemo(() => {
    if (showToolEntries) return false;
    return agents.some(
      (agent) =>
        Array.isArray(agent?.events) &&
        agent.events.some((entry) => entry && entry.type === "tool"),
    );
  }, [agents, showToolEntries]);
  const browserSessionContexts = React.useMemo(() => {
    const sessions = new Map();
    agents.forEach((agent) => {
      const events = Array.isArray(agent?.events) ? agent.events : [];
      events.forEach((entry) => {
        const context = getBrowserSessionToolContext(entry);
        if (!context?.sessionId) return;
        const existing = sessions.get(context.sessionId);
        if (!existing || context.timestamp >= existing.timestamp) {
          sessions.set(context.sessionId, context);
        }
      });
    });
    return sessions;
  }, [agents]);
  const activeBrowserSession = React.useMemo(() => {
    const sessionId =
      browserSessionPopup && typeof browserSessionPopup.sessionId === "string"
        ? browserSessionPopup.sessionId
        : "";
    return sessionId ? browserSessionContexts.get(sessionId) || null : null;
  }, [browserSessionContexts, browserSessionPopup]);
  React.useEffect(() => {
    if (!showSyncInbox) {
      syncInboxInteractedRef.current = false;
      setSyncInboxCollapsed(false);
      return;
    }
    if (syncInboxInteractedRef.current) return;
    setSyncInboxCollapsed(agents.length > 0 || hasInlineToolActivity);
  }, [agents.length, hasInlineToolActivity, showSyncInbox]);
  React.useEffect(() => {
    if (showStandaloneActionHistory) return;
    setActionHistoryCollapsed(false);
    setActionHistoryHidden(false);
  }, [showStandaloneActionHistory]);
  React.useEffect(() => {
    if (!activeBrowserSession?.sessionId) return;
    setBrowserNavigateDraft(activeBrowserSession.currentUrl || "");
  }, [activeBrowserSession?.currentUrl, activeBrowserSession?.sessionId]);
  React.useEffect(() => {
    if (!browserSessionPopup) return undefined;
    const handleEscape = (event) => {
      if (event.key === "Escape") {
        setBrowserSessionPopup(null);
        setBrowserPopupError("");
        setBrowserPopupPendingAction("");
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [browserSessionPopup]);
  const handleRefreshClick = () => {
    if (!backendReady) return;
    onRefreshAgents?.();
    if (isLocalMode) {
      fetchRuntimeStatus();
    } else {
      setRuntimeStatus(null);
    }
    if (isLocalMode && usingProviderRuntime) {
      fetchProviderStatus();
      fetchProviderModels();
      fetchProviderLogs({ reset: true });
    } else {
      const modelName =
        runtimeStatus?.model || state.transformerModel || state.localModel;
      if (modelName && !isLocalRuntimeEntry(modelName)) {
        fetchModelVerify(modelName, { force: true });
      }
    }
    if (isCalendar) {
      onRefreshCalendar?.();
    }
  };
  const handleShowHidden = () => {
    setHiddenAgents({});
    setActionHistoryHidden(false);
  };

  const ensureActionHistoryDetails = React.useCallback(
    async (group) => {
      const actionsForGroup = Array.isArray(group?.actions) ? group.actions : [];
      const pendingIds = actionsForGroup
        .map((action) => String(action?.id || "").trim())
        .filter(Boolean)
        .filter((actionId) => {
          const current = actionHistoryDetails[actionId];
          return !current?.action && !current?.loading;
        });
      if (!pendingIds.length) return;
      setActionHistoryDetails((prev) => {
        const next = { ...prev };
        pendingIds.forEach((actionId) => {
          next[actionId] = {
            ...(next[actionId] || {}),
            open: true,
            loading: true,
            error: "",
          };
        });
        return next;
      });
      await Promise.all(
        pendingIds.map(async (actionId) => {
          try {
            const res = await axios.get(`/api/actions/${encodeURIComponent(actionId)}`);
            setActionHistoryDetails((prev) => ({
              ...prev,
              [actionId]: {
                loading: false,
                error: "",
                open: true,
                action: res?.data?.action || null,
              },
            }));
          } catch (err) {
            const detail =
              err?.response?.data?.detail || err?.message || "Failed to load action diff.";
            setActionHistoryDetails((prev) => ({
              ...prev,
              [actionId]: {
                loading: false,
                error: String(detail),
                open: true,
                action: null,
              },
            }));
          }
        }),
      );
    },
    [actionHistoryDetails],
  );

  const toggleActionHistory = React.useCallback(
    async (group) => {
      const nextKey = String(group?.key || "").trim();
      if (!nextKey) return;
      setActionHistoryFeedback("");
      if (openActionHistoryKey === nextKey) {
        setOpenActionHistoryKey("");
        return;
      }
      setOpenActionHistoryKey(nextKey);
      await ensureActionHistoryDetails(group);
    },
    [ensureActionHistoryDetails, openActionHistoryKey],
  );

  const runActionHistoryRevert = React.useCallback(
    async (key, payload, successMessage) => {
      if (!backendReady) return;
      setActionHistoryPendingKey(key);
      setActionHistoryFeedback("");
      try {
        const res = await axios.post("/api/actions/revert", payload);
        const actionSummary = res?.data?.action?.summary;
        setActionHistoryFeedback(actionSummary || successMessage);
        onRefreshAgents?.();
      } catch (err) {
        const detail = err?.response?.data?.detail || "Failed to revert action.";
        setActionHistoryFeedback(String(detail));
      } finally {
        setActionHistoryPendingKey("");
      }
    },
    [backendReady, onRefreshAgents],
  );

  const submitSyncReviewDecision = React.useCallback(
    async (review, decision) => {
      const reviewId = String(review?.id || "").trim();
      const sourceLabel = String(review?.source_label || "remote device").trim();
      if (!backendReady || !reviewId) return;
      setSyncReviewPendingKey(`${decision}:${reviewId}`);
      setSyncReviewFeedback("");
      try {
        await axios.post(
          `/api/sync/reviews/${encodeURIComponent(reviewId)}/${decision}`,
          { note: "" },
        );
        setSyncReviewFeedback(
          `${decision === "approve" ? "Approved" : "Rejected"} sync from ${sourceLabel}.`,
        );
        await onRefreshAgents?.();
      } catch (err) {
        const detail =
          err?.response?.data?.detail ||
          err?.message ||
          "Failed to update sync review.";
        setSyncReviewFeedback(String(detail));
      } finally {
        setSyncReviewPendingKey("");
      }
    },
    [backendReady, onRefreshAgents],
  );

  const toggleSyncInboxCollapsed = React.useCallback(() => {
    syncInboxInteractedRef.current = true;
    setSyncInboxCollapsed((prev) => !prev);
  }, []);

  const renderSyncInbox = React.useCallback(() => {
    if (!showSyncInbox) return null;

    const renderReviewCard = (review, mode = "pending") => {
      if (!review || typeof review !== "object") return null;
      const reviewId = String(review.id || "").trim();
      if (!reviewId) return null;
      const sourceLabel = String(review.source_label || "remote device").trim() || "remote device";
      const requestedSections = Array.isArray(review.requested_section_labels)
        ? review.requested_section_labels.filter(Boolean)
        : [];
      const requestedCopy = requestedSections.length
        ? requestedSections.join(" + ")
        : "sync data";
      const status = String(review.status || mode).trim().toLowerCase() || mode;
      const timestamp =
        formatReviewTimestamp(review.updated_at || review.created_at) ||
        formatReviewTimestamp(review.created_at);
      const namespaceLabel = String(review.effective_namespace || "").trim();
      const note = String(review.note || "").trim();
      const pendingKey = `approve:${reviewId}`;
      const rejectKey = `reject:${reviewId}`;

      return (
        <article
          key={`${mode}:${reviewId}`}
          className="agent-sync-review-card"
          data-status={status}
        >
          <div className="agent-sync-review-top">
            <div className="agent-sync-review-copy">
              <div className="agent-sync-review-meta">
                <span className={`agent-sync-review-badge is-${status}`}>
                  {status}
                </span>
                {timestamp ? <time>{timestamp}</time> : null}
              </div>
              <p className="agent-sync-review-summary">
                <strong>{sourceLabel}</strong> requested {requestedCopy}.
              </p>
              <p className="agent-sync-review-sections">
                Sections: {requestedCopy}
              </p>
              {namespaceLabel ? (
                <p className="agent-sync-review-note">
                  Target namespace: <code>{namespaceLabel}</code>
                </p>
              ) : null}
              {note ? <p className="agent-sync-review-note">{note}</p> : null}
            </div>
            {mode === "pending" ? (
              <div className="agent-sync-review-actions">
                <button
                  type="button"
                  className="agent-card-control-btn"
                  disabled={Boolean(syncReviewPendingKey)}
                  aria-label={`Approve sync from ${sourceLabel}`}
                  onClick={() => submitSyncReviewDecision(review, "approve")}
                >
                  {syncReviewPendingKey === pendingKey ? "Approving..." : "Approve"}
                </button>
                <button
                  type="button"
                  className="agent-card-control-btn danger"
                  disabled={Boolean(syncReviewPendingKey)}
                  aria-label={`Reject sync from ${sourceLabel}`}
                  onClick={() => submitSyncReviewDecision(review, "reject")}
                >
                  {syncReviewPendingKey === rejectKey ? "Rejecting..." : "Reject"}
                </button>
              </div>
            ) : null}
          </div>
        </article>
      );
    };

    return (
      <section
        className="agent-sync-panel"
        aria-label="sync inbox"
        data-collapsed={syncInboxCollapsed ? "true" : "false"}
      >
        <div className="agent-sync-panel-header">
          <div className="agent-sync-panel-title">
            <h3>sync inbox</h3>
            <span className="agent-sync-panel-subtitle">
              {pendingSyncReviews.length > 0
                ? `${pendingSyncReviews.length} pending`
                : "no pending approvals"}
              {recentSyncReviews.length > 0
                ? ` · ${recentSyncReviews.length} recent`
                : ""}
            </span>
          </div>
          <div className="agent-sync-panel-actions">
            <button
              type="button"
              className="agent-card-control-btn"
              aria-expanded={!syncInboxCollapsed}
              aria-label={syncInboxCollapsed ? "Expand sync inbox" : "Collapse sync inbox"}
              onClick={toggleSyncInboxCollapsed}
              title={syncInboxCollapsed ? "Expand sync inbox" : "Collapse sync inbox"}
            >
              {syncInboxCollapsed ? "+" : "-"}
            </button>
            <button
              type="button"
              className="agent-card-control-btn"
              onClick={() => navigate("/knowledge?tab=sync")}
            >
              Open sync
            </button>
          </div>
        </div>
        {syncReviewFeedback ? (
          <p className="status-note" role="status">
            {syncReviewFeedback}
          </p>
        ) : null}
        {!syncInboxCollapsed && pendingSyncReviews.length > 0 ? (
          <div className="agent-sync-review-list">
            {pendingSyncReviews.map((review) => renderReviewCard(review, "pending"))}
          </div>
        ) : null}
        {!syncInboxCollapsed && recentSyncReviews.length > 0 ? (
          <div className="agent-sync-review-history">
            <div className="agent-sync-history-label">recent decisions</div>
            <div className="agent-sync-review-list">
              {recentSyncReviews.map((review) => renderReviewCard(review, "recent"))}
            </div>
          </div>
        ) : null}
      </section>
    );
  }, [
    navigate,
    pendingSyncReviews,
    recentSyncReviews,
    showSyncInbox,
    submitSyncReviewDecision,
    syncInboxCollapsed,
    syncReviewFeedback,
    syncReviewPendingKey,
    toggleSyncInboxCollapsed,
  ]);

  const renderActionHistoryPopover = React.useCallback(
    (group) => {
      if (!group || openActionHistoryKey !== group.key) return null;
      return (
        <div className="agent-history-popout" role="dialog" aria-label="Work history">
          <div className="agent-history-popout-header">
            <div>
              <strong>work history</strong>
              <div className="agent-history-popout-meta">
                {group.responseLabel} · {group.actions.length} tracked
                {group.actions.length === 1 ? " change" : " changes"}
              </div>
            </div>
            {group.responseId && (
              <button
                type="button"
                className="agent-card-control-btn"
                disabled={actionHistoryPendingKey === `response:${group.responseId}`}
                onClick={() =>
                  runActionHistoryRevert(
                    `response:${group.responseId}`,
                    {
                      response_id: group.responseId,
                      conversation_id: group.conversationId,
                      force: false,
                    },
                    `Reverted ${group.responseLabel}.`,
                  )
                }
              >
                Revert set
              </button>
            )}
          </div>
          {actionHistoryFeedback ? <p className="status-note">{actionHistoryFeedback}</p> : null}
          <div className="agent-history-list">
            {group.actions.map((action) => {
              const detail = actionHistoryDetails[action.id];
              const detailItems = Array.isArray(detail?.action?.items)
                ? detail.action.items
                : [];
              return (
                <div key={action.id} className="agent-history-item">
                  <div className="agent-history-item-header">
                    <div className="agent-history-item-copy">
                      <div className="agent-history-item-meta">
                        <span className="agent-activity-name">{action.name || "write"}</span>
                        <span className="agent-activity-status">
                          {action.status || "saved"}
                        </span>
                        {action.item_count > 0 && (
                          <span className="action-item-count">
                            {action.item_count} item{action.item_count === 1 ? "" : "s"}
                          </span>
                        )}
                        {formatTimestamp(action.created_at_ts || action.timestamp) && (
                          <time>{formatTimestamp(action.created_at_ts || action.timestamp)}</time>
                        )}
                      </div>
                      <p className="agent-history-item-summary">
                        {action.summary || action.name || "Tracked change"}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={
                        !action.revertible || actionHistoryPendingKey === `action:${action.id}`
                      }
                      onClick={() =>
                        runActionHistoryRevert(
                          `action:${action.id}`,
                          { action_ids: [action.id], force: false },
                          `Reverted ${action.summary || action.name || "action"}.`,
                        )
                      }
                    >
                      Revert
                    </button>
                  </div>
                  {detail?.loading ? <p className="status-note">Loading diff…</p> : null}
                  {detail?.error ? <p className="status-note">{detail.error}</p> : null}
                  {detailItems.length ? (
                    <div className="agent-history-diff-list">
                      {detailItems.map((item) => {
                        const docsHref = buildDocsHref(item);
                        return (
                          <div
                            key={`${action.id}:${item.id || item.resource_key}`}
                            className="agent-history-diff-item"
                          >
                            <div className="agent-history-diff-meta">
                              <strong>{item.label || item.resource_id}</strong>
                              <div className="agent-history-diff-actions">
                                <span className="agent-activity-status">
                                  {item.operation || "update"}
                                </span>
                                <span className="action-item-count">
                                  {item.section || item.resource_type}
                                </span>
                                {docsHref && (
                                  <button
                                    type="button"
                                    className="agent-card-control-btn"
                                    onClick={() => navigate(docsHref)}
                                  >
                                    Open in docs
                                  </button>
                                )}
                              </div>
                            </div>
                            <pre className="agent-history-diff">
                              {item?.diff?.unified || buildFallbackDiff(item)}
                            </pre>
                          </div>
                        );
                      })}
                    </div>
                  ) : !detail?.loading && !detail?.error ? (
                    <p className="status-note">No diff details available.</p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      );
    },
    [
      actionHistoryDetails,
      actionHistoryFeedback,
      actionHistoryPendingKey,
      navigate,
      openActionHistoryKey,
      runActionHistoryRevert,
    ],
  );

  const resolveContinueTarget = React.useCallback(
    (target) => {
      const overrideMode =
        typeof target?.mode === "string" ? target.mode.trim().toLowerCase() : "";
      const overrideModel =
        typeof target?.model === "string" ? target.model.trim() : "";
      const overrideWorkflow =
        typeof target?.workflow === "string" ? target.workflow.trim() : "";
      const mode = overrideMode || (state.backendMode || "api").toLowerCase();
      let model =
        mode === "local"
          ? state.localModel || state.transformerModel || state.apiModel
          : mode === "server"
            ? state.transformerModel || state.apiModel
            : state.apiModel;
      if (overrideModel) {
        model = overrideModel;
      }
      return {
        mode,
        model,
        workflow: overrideWorkflow || state.workflowProfile || "default",
      };
    },
    [
      state.apiModel,
      state.backendMode,
      state.localModel,
      state.transformerModel,
      state.workflowProfile,
    ],
  );

  const maybeContinueBatch = React.useCallback(
    async (
      {
        sessionId,
        messageId,
        toolUpdate = null,
      },
      continueTarget,
      options = {},
    ) => {
      if (!sessionId || !messageId) return;
      const force = options.force === true;
      const messageEntry = conversationById.get(messageId);
      const baseTools = Array.isArray(messageEntry?.tools) ? [...messageEntry.tools] : [];
      const mergedTools = mergeToolUpdate(baseTools, toolUpdate);
      if (toolUpdate) {
        setState((prev) => {
          const updated = Array.isArray(prev.conversation)
            ? [...prev.conversation]
            : [];
          const mIdx = updated.findIndex((m) => m && m.id === messageId);
          if (mIdx === -1) return prev;
          const msgEntry = { ...(updated[mIdx] || {}) };
          msgEntry.tools = mergedTools;
          updated[mIdx] = msgEntry;
          return { ...prev, conversation: updated };
        });
      }
      const batch = buildToolContinuationBatch(mergedTools);
      if (!batch) return;
      const batchSignature = buildToolContinuationSignature(batch);
      const semanticBatchSignature = buildToolContinuationSignature(batch, {
        includeIds: false,
      });
      if (
        !force &&
        (hasMatchingToolContinuationSignature(messageEntry?.metadata, batch) ||
          hasMatchingToolContinuationSignature(messageEntry?.metadata, batch, {
            includeIds: false,
          }))
      ) {
        return;
      }
      if (toolContinueLocksRef.current.has(messageId)) return;
      toolContinueLocksRef.current.add(messageId);
      try {
        const thinkingValue = state.thinkingMode || "auto";
        const thinkingPayload =
          thinkingValue === "auto" ? {} : { thinking: thinkingValue };
        const { mode, model, workflow } = resolveContinueTarget(continueTarget);
        const res = await axios.post("/api/chat/continue", {
          session_id: sessionId,
          message_id: messageId,
          model,
          mode,
          workflow,
          ...thinkingPayload,
          tools: batch,
        });
        const continuation = res.data?.message || "";
        const md = res.data?.metadata || {};
        if (continuation) {
          applyContinuation(messageId, continuation, {
            ...md,
            ...(batchSignature && !md?.tool_continue_signature
              ? { tool_continue_signature: batchSignature }
              : {}),
            ...(semanticBatchSignature &&
            !md?.tool_continue_semantic_signature
              ? {
                  tool_continue_semantic_signature: semanticBatchSignature,
                }
              : {}),
          });
        }
      } catch (err) {
        console.error("Auto-continue failed", err);
      } finally {
        toolContinueLocksRef.current.delete(messageId);
      }
    },
    [
      applyContinuation,
      conversationById,
      resolveContinueTarget,
      setState,
      state.thinkingMode,
    ],
  );

  const captureCameraToolResult = React.useCallback(async () => {
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== "function"
    ) {
      return buildToolOutcomeResult("error", "Camera capture is unavailable in this client.");
    }
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: state.preferredCameraDeviceId
          ? { deviceId: { ideal: state.preferredCameraDeviceId } }
          : true,
        audio: false,
      });
      const video = document.createElement("video");
      video.playsInline = true;
      video.muted = true;
      video.srcObject = stream;
      await video.play();
      await new Promise((resolve) => {
        if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
          resolve(true);
          return;
        }
        video.onloadedmetadata = () => resolve(true);
      });
      const width = video.videoWidth || 1280;
      const height = video.videoHeight || 720;
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return buildToolOutcomeResult("error", "Could not access camera frame buffer.");
      }
      ctx.drawImage(video, 0, 0, width, height);
      const blob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/png");
      });
      if (!(blob instanceof Blob)) {
        return buildToolOutcomeResult("error", "Camera capture failed.");
      }
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const file = new File([blob], `camera-tool-${stamp}.png`, {
        type: "image/png",
      });
      const formData = new FormData();
      formData.append("file", file);
      formData.append("source", "camera");
      const res = await axios.post("/api/captures/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return buildToolOutcomeResult(
        "invoked",
        "Captured camera image.",
        res.data || null,
        true,
      );
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err?.message || "Camera capture failed.";
      return buildToolOutcomeResult("error", String(detail));
    } finally {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    }
  }, [state.preferredCameraDeviceId]);

  const resolveClientTool = React.useCallback(
    async (entry) => {
      const sessionId = entry.session_id || state.sessionId || null;
      const messageId = entry.message_id || entry.chain_id || null;
      let result = null;
      let status = "error";
      if (entry.name === "camera.capture") {
        result = await captureCameraToolResult();
        status = String(result?.status || "error").toLowerCase();
      } else {
        result = buildToolOutcomeResult(
          "error",
          `Client-side resolution is not implemented for ${entry.name || "this tool"}.`,
        );
      }
      const resp = await axios.post("/api/tools/client-resolve", {
        request_id: entry.id,
        status:
          status === "denied" || status === "error" ? status : "invoked",
        result,
        args: entry.args || {},
        name: entry.name,
        session_id: sessionId,
        message_id: messageId,
        chain_id: entry.chain_id || messageId || sessionId,
      });
      return {
        status: String(resp?.data?.status || status || "error").toLowerCase(),
        result:
          typeof resp?.data?.result !== "undefined" ? resp.data.result : result,
      };
    },
    [captureCameraToolResult, state.sessionId],
  );

  React.useEffect(() => {
    if (!backendReady) return;
    const approvalLevel = state.approvalLevel || "all";
    const candidates = [];
    agents.forEach((agent) => {
      const events = Array.isArray(agent?.events) ? agent.events : [];
      events.forEach((entry) => {
        if (!entry || entry.type !== "tool") return;
        const toolName = String(entry.name || "").trim();
        if (!CLIENT_RESOLUTION_TOOLS.has(toolName)) return;
        if (!shouldAutoApproveTool(approvalLevel, toolName)) return;
        if (getEffectiveToolStatus(entry) !== "proposed") return;
        const requestId = String(entry.id || entry.request_id || "").trim();
        if (!requestId || autoToolResolveLocksRef.current.has(requestId)) return;
        candidates.push({ requestId, entry });
      });
    });
    candidates.forEach(({ requestId, entry }) => {
      autoToolResolveLocksRef.current.add(requestId);
      (async () => {
        try {
          const resp = await resolveClientTool(entry);
          const status = String(resp?.status || "").toLowerCase();
          const resolvedResult =
            typeof resp?.result !== "undefined"
              ? resp.result
              : fallbackResultForStatus(status);
          await maybeContinueBatch(
            {
              sessionId: entry.session_id || state.sessionId || null,
              messageId: entry.message_id || entry.chain_id || null,
              toolUpdate: {
                id: entry.id || entry.request_id,
                name: entry.name,
                args: entry.args || {},
                ...(typeof resolvedResult !== "undefined"
                  ? { result: resolvedResult }
                  : {}),
                status: status || "invoked",
              },
            },
            null,
          );
        } catch (err) {
          console.error("Auto-resolving client tool failed", err);
        } finally {
          autoToolResolveLocksRef.current.delete(requestId);
        }
      })();
    });
  }, [
    agents,
    backendReady,
    maybeContinueBatch,
    resolveClientTool,
    state.approvalLevel,
    state.sessionId,
  ]);

  React.useEffect(() => {
    const conversation = Array.isArray(state.conversation) ? state.conversation : [];
    conversation.forEach((message) => {
      if (!message || typeof message !== "object") return;
      const metadata =
        message.metadata && typeof message.metadata === "object" ? message.metadata : {};
      if (!metadata.tool_response_pending) return;
      const messageId = message.id || message.message_id || null;
      if (!messageId) return;
      const tools = Array.isArray(message.tools) ? message.tools : [];
      if (!buildToolContinuationBatch(tools)) return;
      void maybeContinueBatch(
        {
          sessionId: state.sessionId || null,
          messageId,
          toolUpdate: null,
        },
        metadata.continue_target || null,
      );
    });
  }, [maybeContinueBatch, state.conversation, state.sessionId]);

  const openBrowserSessionInspector = React.useCallback((computer) => {
    const sessionId =
      computer && typeof computer.sessionId === "string" ? computer.sessionId.trim() : "";
    if (!sessionId) return;
    setBrowserSessionPopup({ sessionId });
    setBrowserPopupError("");
    setBrowserPopupPendingAction("");
    setBrowserNavigateDraft(computer.currentUrl || "");
    setBrowserTypeDraft("");
    setBrowserKeyDraft("Enter");
  }, []);

  const invokeBrowserSessionTool = React.useCallback(
    async (toolName, args = {}) => {
      if (!activeBrowserSession?.sessionId) {
        throw new Error("Browser session is unavailable.");
      }
      const payload = {
        name: toolName,
        args: {
          session_id: activeBrowserSession.sessionId,
          ...args,
        },
        session_id:
          activeBrowserSession.entry?.session_id || activeBrowserSession.sessionId,
        message_id:
          activeBrowserSession.entry?.message_id ||
          activeBrowserSession.messageId ||
          undefined,
        chain_id:
          activeBrowserSession.entry?.chain_id ||
          activeBrowserSession.chainId ||
          undefined,
      };
      const resp = await axios.post("/api/tools/invoke", payload);
      onRefreshAgents?.();
      return resp?.data?.result;
    },
    [activeBrowserSession, onRefreshAgents],
  );

  const runBrowserSessionAction = React.useCallback(
    async (actionLabel, callback) => {
      setBrowserPopupPendingAction(actionLabel);
      setBrowserPopupError("");
      try {
        await callback();
      } catch (err) {
        console.error(`Browser popup action failed: ${actionLabel}`, err);
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "Browser action failed.";
        setBrowserPopupError(String(detail));
      } finally {
        setBrowserPopupPendingAction("");
      }
    },
    [],
  );

  const handleBrowserPopupObserve = React.useCallback(() => {
    if (!activeBrowserSession?.sessionId || browserPopupPendingAction) return;
    void runBrowserSessionAction("observe", async () => {
      await invokeBrowserSessionTool("computer.observe");
    });
  }, [
    activeBrowserSession,
    browserPopupPendingAction,
    invokeBrowserSessionTool,
    runBrowserSessionAction,
  ]);

  const handleBrowserPopupNavigate = React.useCallback(
    (event) => {
      event?.preventDefault?.();
      const targetUrl = browserNavigateDraft.trim();
      if (!targetUrl || browserPopupPendingAction) return;
      void runBrowserSessionAction("navigate", async () => {
        await invokeBrowserSessionTool("computer.navigate", { url: targetUrl });
      });
    },
    [
      browserNavigateDraft,
      browserPopupPendingAction,
      invokeBrowserSessionTool,
      runBrowserSessionAction,
    ],
  );

  const handleBrowserPopupType = React.useCallback(
    (event) => {
      event?.preventDefault?.();
      const text = browserTypeDraft;
      if (!text || browserPopupPendingAction) return;
      void runBrowserSessionAction("type", async () => {
        await invokeBrowserSessionTool("computer.act", {
          actions: [{ type: "type", text }],
        });
      });
    },
    [
      browserPopupPendingAction,
      browserTypeDraft,
      invokeBrowserSessionTool,
      runBrowserSessionAction,
    ],
  );

  const handleBrowserPopupKeypress = React.useCallback(
    (event) => {
      event?.preventDefault?.();
      const keys = browserKeyDraft.trim();
      if (!keys || browserPopupPendingAction) return;
      void runBrowserSessionAction("keypress", async () => {
        await invokeBrowserSessionTool("computer.act", {
          actions: [{ type: "keypress", keys }],
        });
      });
    },
    [
      browserKeyDraft,
      browserPopupPendingAction,
      invokeBrowserSessionTool,
      runBrowserSessionAction,
    ],
  );

  const handleBrowserPreviewClick = React.useCallback(
    (event) => {
      if (!activeBrowserSession?.sessionId || browserPopupPendingAction) return;
      const img = event.currentTarget;
      const rect = img.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const width =
        img.naturalWidth ||
        activeBrowserSession.session?.width ||
        activeBrowserSession.entry?.result?.session?.width ||
        0;
      const height =
        img.naturalHeight ||
        activeBrowserSession.session?.height ||
        activeBrowserSession.entry?.result?.session?.height ||
        0;
      if (!width || !height) return;
      const x = Math.max(
        0,
        Math.min(
          width,
          Math.round(((event.clientX - rect.left) / rect.width) * width),
        ),
      );
      const y = Math.max(
        0,
        Math.min(
          height,
          Math.round(((event.clientY - rect.top) / rect.height) * height),
        ),
      );
      void runBrowserSessionAction("click", async () => {
        await invokeBrowserSessionTool("computer.act", {
          actions: [{ type: "click", x, y, button: "left" }],
        });
      });
    },
    [
      activeBrowserSession,
      browserPopupPendingAction,
      invokeBrowserSessionTool,
      runBrowserSessionAction,
    ],
  );

  const renderToolActions = (entry) => {
      const normalizedStatus = getEffectiveToolStatus(entry);
      const targetChain = entry.chain_id || entry.message_id;
      const sessionForEntry = entry.session_id || state.sessionId || null;
      const messageForEntry = entry.message_id || entry.chain_id || null;
      const messageEntry = messageForEntry ? conversationById.get(messageForEntry) : null;
      const resolvedToolsSource =
        Array.isArray(messageEntry?.tools) && messageEntry.tools.length
          ? messageEntry.tools
          : [entry];
      const resolvedBatch = buildToolContinuationBatch(resolvedToolsSource);
      const canContinueResolvedBatch = Boolean(
        sessionForEntry && messageForEntry && resolvedBatch,
      );
      if (normalizedStatus && normalizedStatus !== "proposed" && !canContinueResolvedBatch) {
        return null;
      }
      const buildDecisionPayload = (decision, overrideArgs, overrideName) => {
        const hasArgs =
          entry.args && typeof entry.args === "object" && Object.keys(entry.args).length > 0;
        const effectiveArgs = overrideArgs ?? (hasArgs ? entry.args : undefined);
        const payload = {
          request_id: entry.id,
          decision,
          name: (overrideName || entry.name || "").trim() || entry.name,
          session_id: sessionForEntry,
          message_id: messageForEntry,
          chain_id: targetChain || messageForEntry || sessionForEntry || null,
        };
        if (typeof effectiveArgs !== "undefined") {
          payload.args = effectiveArgs;
        }
        return payload;
      };

      if (normalizedStatus && normalizedStatus !== "proposed") {
        return (
          <div className="agent-tool-actions">
            <button
              type="button"
              className="tool-action-btn continue"
              onClick={async (event) => {
                event.stopPropagation();
                await maybeContinueBatch(
                  {
                    sessionId: sessionForEntry,
                    messageId: messageForEntry,
                    toolUpdate: null,
                  },
                  null,
                  { force: true },
                );
              }}
            >
              Continue
            </button>
          </div>
        );
      }
      return (
        <div className="agent-tool-actions">
          <button
            type="button"
            className="tool-action-btn accept"
            onClick={async (event) => {
            event.stopPropagation();
            try {
              if (entry.id && CLIENT_RESOLUTION_TOOLS.has(String(entry.name || ""))) {
                const resp = await resolveClientTool(entry);
                const status = String(resp?.status || "").toLowerCase();
                const resolvedResult =
                  typeof resp?.result !== "undefined"
                    ? resp.result
                    : fallbackResultForStatus(status);
                await maybeContinueBatch(
                  {
                    sessionId: sessionForEntry,
                    messageId: messageForEntry,
                    toolUpdate: {
                      id: entry.id,
                      name: entry.name,
                      args: entry.args || {},
                      ...(typeof resolvedResult !== "undefined"
                        ? { result: resolvedResult }
                        : {}),
                      status: status || "invoked",
                    },
                  },
                  null,
                );
              } else if (entry.id) {
                const resp = await axios.post(
                  "/api/tools/decision",
                  buildDecisionPayload("accept"),
                );
                const status = String(resp?.data?.status || "").toLowerCase();
                if (status === "invoked" || status === "error" || status === "denied") {
                  const resolvedResult =
                    typeof resp?.data?.result !== "undefined"
                      ? resp.data?.result
                      : fallbackResultForStatus(status);
                  await maybeContinueBatch(
                    {
                      sessionId: sessionForEntry,
                      messageId: messageForEntry,
                      toolUpdate: {
                        id: entry.id,
                        name: entry.name,
                        args: entry.args || {},
                        ...(typeof resolvedResult !== "undefined"
                          ? { result: resolvedResult }
                          : {}),
                        status: status || "invoked",
                      },
                    },
                    null,
                  );
                }
              } else {
                try {
                  const resp = await axios.post("/api/tools/invoke", {
                    name: entry.name,
                    args: entry.args || {},
                    chain_id: targetChain,
                    session_id: entry.session_id || state.sessionId,
                    message_id: targetChain,
                  });
                  await maybeContinueBatch(
                    {
                      sessionId: sessionForEntry,
                      messageId: messageForEntry,
                      toolUpdate: {
                        id: entry.id,
                        name: entry.name,
                        args: entry.args || {},
                        ...(typeof resp?.data?.result !== "undefined"
                          ? { result: resp.data?.result }
                          : {}),
                        status: "invoked",
                      },
                    },
                    null,
                  );
                } catch (err) {
                  console.error("Tool invoke failed", err);
                  const detail =
                    err?.response?.data?.detail ||
                    err?.response?.data?.message ||
                    err?.message ||
                    "Tool invoke failed.";
                  const statusCode = err?.response?.status;
                  const safeDetail =
                    statusCode && statusCode >= 500 ? "Tool error." : detail;
                  await maybeContinueBatch(
                    {
                      sessionId: sessionForEntry,
                      messageId: messageForEntry,
                      toolUpdate: {
                        id: entry.id,
                        name: entry.name,
                        args: entry.args || {},
                        result: buildToolOutcomeResult("error", safeDetail),
                        status: "error",
                      },
                    },
                    null,
                  );
                }
              }
            } catch (err) {
              console.error("Tool accept failed", err);
            }
          }}
        >
          Accept
        </button>
        <button
          type="button"
          className="tool-action-btn deny"
          disabled={!entry.id}
          onClick={async (event) => {
            event.stopPropagation();
            if (!entry.id) return;
            try {
              const resp = await axios.post(
                "/api/tools/decision",
                buildDecisionPayload("deny"),
              );
              const status = String(resp?.data?.status || "").toLowerCase();
              if (status === "denied") {
                const resolvedResult =
                  typeof resp?.data?.result !== "undefined"
                    ? resp.data?.result
                    : fallbackResultForStatus(status);
                await maybeContinueBatch(
                  {
                    sessionId: sessionForEntry,
                    messageId: messageForEntry,
                    toolUpdate: {
                      id: entry.id,
                      name: entry.name,
                      args: entry.args || {},
                      ...(typeof resolvedResult !== "undefined"
                        ? { result: resolvedResult }
                        : {}),
                      status: "denied",
                    },
                  },
                  null,
                );
              }
            } catch (err) {
              console.error("Tool deny failed", err);
            }
          }}
        >
          Deny
        </button>
        <button
          type="button"
          className="tool-action-btn edit"
          onClick={async (event) => {
            event.stopPropagation();
            const current = JSON.stringify(entry.args || {}, null, 2);
            setToolEditorState({
              tool: {
                  name: entry.name,
                  args: entry.args || {},
                  id: entry.id,
                  status: entry.status,
                },
                schedulePrefill: (() => {
                  const base =
                    state.selectedCalendarDate instanceof Date
                      ? new Date(state.selectedCalendarDate)
                      : new Date();
                  return {
                    start_time: Math.floor(base.getTime() / 1000),
                    timezone: preferredTimezone,
                    title: `Schedule tool: ${entry.name || "tool"}`,
                  };
                })(),
                onSubmit: async ({ args, name, continueTarget }) => {
                  try {
                    if (entry.id) {
                      const resp = await axios.post(
                        "/api/tools/decision",
                        buildDecisionPayload("accept", args, name),
                      );
                      const status = String(resp?.data?.status || "").toLowerCase();
                      if (status === "invoked" || status === "error" || status === "denied") {
                        const resolvedResult =
                          typeof resp?.data?.result !== "undefined"
                            ? resp.data?.result
                            : fallbackResultForStatus(status);
                        await maybeContinueBatch(
                          {
                            sessionId: sessionForEntry,
                            messageId: messageForEntry,
                            toolUpdate: {
                              id: entry.id,
                              name: (name || entry.name || "").trim() || entry.name,
                              args: args || {},
                              ...(typeof resolvedResult !== "undefined"
                                ? { result: resolvedResult }
                                : {}),
                              status: status || "invoked",
                            },
                          },
                          continueTarget,
                        );
                      }
                    } else {
                      const resp = await axios.post("/api/tools/invoke", {
                        name: (name || entry.name || "").trim() || entry.name,
                        args: args || {},
                        chain_id: targetChain,
                        session_id: entry.session_id || state.sessionId,
                        message_id: targetChain,
                      });
                      await maybeContinueBatch(
                        {
                          sessionId: sessionForEntry,
                          messageId: messageForEntry,
                          toolUpdate: {
                            id: entry.id,
                            name: (name || entry.name || "").trim() || entry.name,
                            args: args || {},
                            ...(typeof resp?.data?.result !== "undefined"
                              ? { result: resp.data?.result }
                              : {}),
                            status: "invoked",
                          },
                        },
                        continueTarget,
                      );
                    }
                  } catch (err) {
                    console.error("Tool edit/invoke failed", err);
                  }
                },
                onSchedule: async ({ args, name, schedule }) => {
                  if (!schedule || !schedule.event_id) {
                    throw new Error("Missing schedule details.");
                  }
                  const eventId = String(schedule.event_id);
                  const toolName =
                    (name || entry.name || "").trim() || entry.name || "tool";
                  const toolArgs = args || {};
                  const chain =
                    targetChain || messageForEntry || sessionForEntry || undefined;
                  const continueInline =
                    schedule.conversation_mode !== "new_chat";
                  try {
                    await axios.post(
                      `/api/calendar/events/${encodeURIComponent(eventId)}`,
                      {
                        id: eventId,
                        title: schedule.title || `Schedule tool: ${toolName}`,
                        description: schedule.description,
                        location: schedule.location,
                        start_time: schedule.start_time,
                        end_time: schedule.end_time,
                        timezone: schedule.timezone,
                        status: schedule.status || "scheduled",
                      },
                    );
                    const requestId = entry.id ? String(entry.id) : eventId;
                    await axios.post("/api/tools/schedule", {
                      request_id: requestId,
                      event_id: eventId,
                      name: toolName,
                      args: toolArgs,
                      prompt: schedule.prompt,
                      conversation_mode: schedule.conversation_mode,
                      session_id: continueInline ? sessionForEntry || undefined : undefined,
                      message_id: continueInline ? messageForEntry || undefined : undefined,
                      chain_id: continueInline ? chain : undefined,
                    });

                    if (messageForEntry && entry.id) {
                      setState((prev) => {
                        const updated = Array.isArray(prev.conversation)
                          ? [...prev.conversation]
                          : [];
                        const idx = updated.findIndex(
                          (m) => m && m.id === messageForEntry,
                        );
                        if (idx === -1) return prev;
                        const tools = Array.isArray(updated[idx]?.tools)
                          ? [...updated[idx].tools]
                          : [];
                        const tIdx = tools.findIndex(
                          (t) =>
                            t &&
                            (t.id === entry.id || t.request_id === entry.id),
                        );
                        if (tIdx === -1) return prev;
                        tools[tIdx] = {
                          ...tools[tIdx],
                          status: "scheduled",
                          result: { scheduled_event_id: eventId },
                        };
                        updated[idx] = { ...updated[idx], tools };
                        return { ...prev, conversation: updated };
                      });
                    }
                  } catch (err) {
                    console.error("Failed to schedule tool", err);
                    throw err;
                  } finally {
                    onRefreshCalendar?.();
                  }
                },
              });
            }}
          >
          Edit
        </button>
      </div>
    );
  };

  const renderAgentCard = (agent) => {
    if (!agent) return null;
    const tone = statusTone(agent.status);
    const activity = Array.isArray(agent.events) ? agent.events : [];
    const filteredActivity = activity.filter(
      (entry) =>
        entry &&
        entry.type !== "content" &&
        (showToolEntries || entry.type !== "tool"),
    );
    const latestThought = [...filteredActivity]
      .reverse()
      .find((entry) => entry.type === "thought");
    const lastMessage = latestThought?.content || agent.summary || "";
    const agentKey =
      agent.id || agent.agent_id || agent.session_id || agent.chain_id || agent.label;
    const agentKeyString =
      agentKey === null || agentKey === undefined ? null : String(agentKey);
    // TODO: Replace token estimates with per-agent runtime telemetry when workers expose metrics.
    const resources = agent.resources && typeof agent.resources === "object"
      ? agent.resources
      : null;
    const promptTokens = resources?.prompt_tokens_total;
    const completionTokens = resources?.completion_tokens_total;
    const totalTokens = resources?.total_tokens;
    const showTokens =
      typeof promptTokens === "number" ||
      typeof completionTokens === "number" ||
      typeof totalTokens === "number";
    const isHidden = !!(agentKeyString && hiddenAgents[agentKeyString]);
    const isCompact = !!(agentKeyString && collapsedAgents[agentKeyString]);
    const showAllActivity = !!(agentKeyString && expandedAgents[agentKeyString]);
    const cardHasFocus =
      normalizedFocus &&
      ((normalizedFocus.agentId && agentKeyString === normalizedFocus.agentId) ||
        filteredActivity.some((entry) => matchesFocus(entry)));
    const activeClass = agent.status === "active" ? " active" : "";
    const cardClass = `agent-card${activeClass}${cardHasFocus ? " focused" : ""}${
      isCompact ? " compact" : ""
    }`;
    const activityList = showAllActivity
      ? [...filteredActivity].reverse()
      : filteredActivity.slice(-6).reverse();
    const canExpand = filteredActivity.length > 6;
    const toggleCompact = () => {
      if (!agentKeyString) return;
      setCollapsedAgents((prev) => ({
        ...prev,
        [agentKeyString]: !isCompact,
      }));
    };
    const toggleExpanded = () => {
      if (!agentKeyString) return;
      setExpandedAgents((prev) => ({
        ...prev,
        [agentKeyString]: !showAllActivity,
      }));
    };
    const hideAgent = () => {
      if (!agentKeyString) return;
      setHiddenAgents((prev) => ({
        ...prev,
        [agentKeyString]: true,
      }));
    };
    if (isHidden) return null;
    return (
      <article
        key={agent.id}
        className={cardClass}
        data-agent-id={agentKeyString || undefined}
      >
        <header className="agent-card-header">
          <div className="agent-card-meta">
            <span className="agent-status-dot" style={{ backgroundColor: tone.hue }} />
            <h3 title={agent.label}>{agent.label}</h3>
          </div>
          <div className="agent-card-actions">
            <div className="agent-card-submeta">
              {agent.status && <span className="agent-status-label">{tone.label}</span>}
              {agent.updatedAt && (
                <time className="agent-updated-at" dateTime={new Date(agent.updatedAt * 1000).toISOString()}>
                  {formatTimestamp(agent.updatedAt)}
                </time>
              )}
            </div>
            <div className="agent-card-controls">
              {canExpand && (
                <button
                  type="button"
                  className={`agent-card-control-btn${showAllActivity ? " is-active" : ""}`}
                  onClick={toggleExpanded}
                  title={showAllActivity ? "Show recent activity" : "Show full activity"}
                  aria-label={showAllActivity ? "Show recent activity" : "Show full activity"}
                  disabled={!agentKeyString}
                >
                  {showAllActivity ? "recent" : "show all"}
                </button>
              )}
              <button
                type="button"
                className={`agent-card-control-btn${isCompact ? " is-active" : ""}`}
                onClick={toggleCompact}
                title={isCompact ? "Expand agent card" : "Compact agent card"}
                aria-label={isCompact ? "Expand agent card" : "Compact agent card"}
                disabled={!agentKeyString}
              >
                {isCompact ? "expand" : "compact"}
              </button>
              <button
                type="button"
                className="agent-card-control-btn danger"
                onClick={hideAgent}
                title="Hide agent card"
                aria-label="Hide agent card"
                disabled={!agentKeyString}
              >
                hide
              </button>
            </div>
          </div>
        </header>
        {!isCompact && lastMessage && (
          <p className="agent-card-summary" title={lastMessage}>
            {lastMessage}
          </p>
        )}
        {!isCompact && showTokens && (
          <div className="agent-card-resources">
            <span className="agent-resource-pill">
              in {formatTokenCount(promptTokens || 0)}
            </span>
            <span className="agent-resource-pill">
              out {formatTokenCount(completionTokens || 0)}
            </span>
            <span className="agent-resource-pill">
              total {formatTokenCount(totalTokens || 0)}
            </span>
            <span className="agent-resource-note">
              token estimates
            </span>
          </div>
        )}
        {!isCompact && (
          <ul className="agent-activity-list">
            {activityList.map((entry) => {
              const ts = formatTimestamp(entry.timestamp);
              const status = getEffectiveToolStatus(entry) || normalizeToolStatus(entry.status) || null;
              const displayStatus = status && status !== "active" ? status : null;
              const isProposedTool = entry.type === "tool" && status === "proposed";
              const isResolvedTool = entry.type === "tool" && status && status !== "proposed";
              const eventAgeSeconds =
                typeof entry.timestamp === "number" ? Date.now() / 1000 - entry.timestamp : null;
              const entryFocused = matchesFocus(entry);
              const isAged =
                !entryFocused &&
                eventAgeSeconds !== null &&
                Number.isFinite(eventAgeSeconds) &&
                eventAgeSeconds > 180;
              const chainIdentifier = entry.chain_id || entry.message_id;
              const sourceLabel = (() => {
                const direct = formatModelSourceLabel(entry.mode, entry.model);
                if (direct) return direct;
                if (!chainIdentifier) return "";
                const msg = conversationById.get(chainIdentifier);
                const meta = msg && typeof msg === "object" ? msg.metadata : null;
                return formatModelSourceLabel(meta?.mode, meta?.model);
              })();
              const collapsed =
                chainIdentifier && Object.prototype.hasOwnProperty.call(collapsedChains, chainIdentifier)
                  ? !!collapsedChains[chainIdentifier]
                  : isResolvedTool
                    ? true
                    : isProposedTool
                      ? false
                      : !normalizedFocus || normalizedFocus.chainId !== chainIdentifier;
              const toggleCollapsed = () => {
                if (!chainIdentifier) return;
                setCollapsedChains((prev) => ({
                  ...prev,
                  [chainIdentifier]: !collapsed,
                }));
            };
              const displayType = entry.type === "stream" ? "response" : entry.type;
              const isStream = entry.type === "stream";
              const streamLabel = isStream ? formatStreamLabel(entry) : null;
              const responseHistory =
                entry.type === "tool"
                  ? actionHistoryByResponse.get(
                    String(entry.message_id || entry.chain_id || "").trim(),
                  ) || null
                  : null;
              const bodyText =
                entry.type === "task"
                  ? entry.content || entry.description || "Task update"
                : isStream
                  ? streamLabel || "streaming response"
                  : entry.content || entry.text || entry.message || "...";
            const preview = collapsed ? buildEntryPreview(entry, bodyText) : null;
            return (
              <li
                key={`${agent.id}-${entry.timestamp}-${entry.type}-${entry.name || entry.id || "log"}`}
                className={`agent-activity agent-activity-${entry.type}${
                    isProposedTool ? " proposed" : ""
                  }${isResolvedTool ? " resolved" : ""}${isAged ? " aged" : ""}${
                    entryFocused ? " focused" : ""
                  }`}
                  data-tool-id={
                    entry.id || entry.request_id
                      ? String(entry.id || entry.request_id)
                      : undefined
                  }
                  data-chain-id={chainIdentifier ? String(chainIdentifier) : undefined}
                  data-agent-id={agentKeyString || undefined}
                  onClick={() => {
                    if (chainIdentifier) {
                      onSelectMessage?.(chainIdentifier);
                    }
                  }}
                  role={chainIdentifier ? "button" : undefined}
                  tabIndex={chainIdentifier ? 0 : undefined}
                  onKeyDown={(event) => {
                    if ((event.key === "Enter" || event.key === " ") && chainIdentifier) {
                      event.preventDefault();
                      onSelectMessage?.(chainIdentifier);
                    }
                  }}
                >
                  <div className="agent-activity-meta">
                    <span className="agent-activity-type">{displayType}</span>
                    {entry.type === "tool" && entry.name && (
                      <span className="agent-activity-name">{entry.name}</span>
                    )}
                    {displayStatus && <span className="agent-activity-status">{displayStatus}</span>}
                    {ts && <time>{ts}</time>}
                    {responseHistory && (
                      <button
                        type="button"
                        className="agent-card-control-btn agent-history-toggle"
                        aria-expanded={openActionHistoryKey === responseHistory.key}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleActionHistory(responseHistory);
                        }}
                      >
                        work history ({responseHistory.actions.length})
                      </button>
                    )}
                    {chainIdentifier && (
                      <button
                        type="button"
                        className="agent-activity-toggle"
                        aria-expanded={!collapsed}
                        aria-label={collapsed ? "Expand activity details" : "Collapse activity details"}
                        title={collapsed ? "Expand activity details" : "Collapse activity details"}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleCollapsed();
                        }}
                      >
                        <span className="agent-activity-chevron" aria-hidden="true">
                          {collapsed ? ">" : "v"}
                        </span>
                      </button>
                    )}
                  </div>
                {collapsed && preview?.short && (
                  <div className="agent-activity-preview" title={preview.full}>
                    {preview.short}
                  </div>
                )}
                {!collapsed && (
                  <div className="agent-activity-body">
                    {entry.type === "tool" ? (
                      <>
                        <strong>{entry.name || "tool"}</strong>
                        {sourceLabel && (
                          <div className="agent-activity-source">source: {sourceLabel}</div>
                        )}
                        {entry.args && (
                          <ToolPayloadView
                            value={entry.args}
                            kind="args"
                            toolName={entry.name}
                            compact
                          />
                        )}
                        {typeof entry.result !== "undefined" && entry.result !== null && (
                          <ToolPayloadView
                            value={entry.result}
                            kind="result"
                            toolName={entry.name}
                            compact
                            onOpenComputerSession={openBrowserSessionInspector}
                          />
                        )}
                      </>
                    ) : entry.type === "stream" ? (
                      <div className="agent-activity-stream">
                        <div className="agent-activity-text">{bodyText}</div>
                        <div
                          className="agent-stream-progress"
                          role="progressbar"
                          aria-label="Streaming response"
                        />
                      </div>
                    ) : (
                      <div className="agent-activity-text">{bodyText}</div>
                    )}
                  </div>
                )}
                  {entry.type === "tool" && !collapsed && renderToolActions(entry)}
                  {entry.type === "tool" && responseHistory && renderActionHistoryPopover(responseHistory)}
                </li>
              );
            })}
          </ul>
        )}
      </article>
    );
  };

  const renderBrowserSessionPopup = () => {
    return (
      <BrowserSessionDialog
        isOpen={Boolean(browserSessionPopup)}
        session={activeBrowserSession}
        fallbackSessionId={browserSessionPopup?.sessionId || ""}
        pendingAction={browserPopupPendingAction}
        error={browserPopupError}
        navigateDraft={browserNavigateDraft}
        setNavigateDraft={setBrowserNavigateDraft}
        typeDraft={browserTypeDraft}
        setTypeDraft={setBrowserTypeDraft}
        keyDraft={browserKeyDraft}
        setKeyDraft={setBrowserKeyDraft}
        onClose={() => {
          setBrowserSessionPopup(null);
          setBrowserPopupError("");
          setBrowserPopupPendingAction("");
        }}
        onObserve={handleBrowserPopupObserve}
        onNavigate={handleBrowserPopupNavigate}
        onType={handleBrowserPopupType}
        onKeypress={handleBrowserPopupKeypress}
        onScreenshotClick={handleBrowserPreviewClick}
        idPrefix="agent-console-browser-session"
      />
    );
  };

  const renderCalendar = () => {
    const query = taskQuery.trim().toLowerCase();
    const selected = state.selectedCalendarDate;
    const selectedLabel =
      selected instanceof Date && !Number.isNaN(selected.getTime())
        ? selected.toLocaleDateString([], {
            month: "short",
            day: "numeric",
            year:
              selected.getFullYear() !== new Date().getFullYear()
                ? "numeric"
                : undefined,
          })
        : "";

    const normalized = (events || []).map((event) => {
      const startMs = Number.isFinite(event.sidebarStart)
        ? event.sidebarStart
        : resolveEventTimestamp(event.startDate) ??
          resolveEventTimestamp(event.start_time) ??
          resolveEventTimestamp(event.start) ??
          resolveEventTimestamp(event.start?.dateTime) ??
          resolveEventTimestamp(event.start?.date);
      const endMs = Number.isFinite(event.sidebarEnd)
        ? event.sidebarEnd
        : resolveEventTimestamp(event.endDate) ??
          resolveEventTimestamp(event.end_time) ??
          resolveEventTimestamp(event.end) ??
          resolveEventTimestamp(event.end?.dateTime) ??
          resolveEventTimestamp(event.end?.date);
      const startDate = Number.isFinite(startMs) ? new Date(startMs) : null;
      const endDate = Number.isFinite(endMs) ? new Date(endMs) : null;
      return { event, startMs, endMs, startDate, endDate };
    });

    const filtered = normalized.filter(({ event }) => {
      if (!query) return true;
      const haystack = [
        event.summary,
        event.description,
        event.location,
        event.status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });

    const items = query ? filtered : normalized;
    const emptyMessage = query
      ? "No tasks match this search."
      : "No upcoming tasks.";
    const normalizeTaskStatusKey = (value) => {
      const raw = String(value || "")
        .trim()
        .toLowerCase();
      if (!raw) return "pending";
      if (raw === "proposed") return "scheduled";
      if (["acknowledge", "complete", "completed", "done"].includes(raw)) {
        return "acknowledged";
      }
      if (raw === "skip") return "skipped";
      return raw;
    };
    const promptedReminders = items
      .filter(({ event }) => normalizeTaskStatusKey(event.status) === "prompted")
      .slice(0, 3);

    const persistTask = async (payload) => {
      await axios.post(
        `/api/calendar/events/${encodeURIComponent(payload.id)}`,
        payload,
      );
      onRefreshCalendar?.();
    };

    const openTaskEditor = ({ task = null, taskPrefill = null } = {}) => {
      setToolEditorState({
        mode: "task",
        task,
        taskPrefill,
        onSaveTask: persistTask,
      });
    };

    const taskStatusInfo = (event, startDate, endDate) => {
      const raw = event?.status ? String(event.status).trim() : "";
      const key = normalizeTaskStatusKey(raw);
      const safeKey = key.replace(/[^a-z0-9_-]/g, "-");
      const deadlineMs =
        endDate instanceof Date && !Number.isNaN(endDate.getTime())
          ? endDate.getTime()
          : startDate instanceof Date && !Number.isNaN(startDate.getTime())
            ? startDate.getTime()
            : null;
      const isPast = typeof deadlineMs === "number" ? deadlineMs < Date.now() : false;

      if (key === "acknowledged") {
        return { label: "Done", className: "done", title: "acknowledged" };
      }
      if (key === "skipped") {
        return { label: "Skipped", className: "skipped", title: "skipped" };
      }
      if (key === "prompted") {
        return { label: "Needs review", className: "prompted", title: "prompted" };
      }
        if (key === "proposed" || key === "scheduled") {
          return {
            label: "Scheduled",
            className: "scheduled",
            title: "scheduled",
          };
        }
      if (key === "pending" && isPast) {
        return { label: "Overdue", className: "overdue", title: "pending (overdue)" };
      }

      return { label: raw || "Pending", className: safeKey, title: raw || "" };
    };

    const formatWindow = (startDate, endDate, timezoneName = preferredTimezone) => {
      if (!startDate) return "unscheduled";
      const resolvedTimezone =
        typeof timezoneName === "string" && timezoneName.trim()
          ? timezoneName.trim()
          : preferredTimezone;
      const dateOpts = { weekday: "short", month: "short", day: "numeric" };
      const timeOpts = { hour: "2-digit", minute: "2-digit" };
      const dateLabel = startDate.toLocaleDateString([], {
        ...dateOpts,
        timeZone: resolvedTimezone,
      });
      const timeLabel = startDate.toLocaleTimeString([], {
        ...timeOpts,
        timeZone: resolvedTimezone,
      });
      if (!endDate) return `${dateLabel} @ ${timeLabel}`;
      const sameDay =
        endDate.toLocaleDateString("en-CA", { timeZone: resolvedTimezone }) ===
        startDate.toLocaleDateString("en-CA", { timeZone: resolvedTimezone });
      if (sameDay) {
        const endLabel = endDate.toLocaleTimeString([], {
          ...timeOpts,
          timeZone: resolvedTimezone,
        });
        return `${dateLabel} @ ${timeLabel} - ${endLabel}`;
      }
      const endDateLabel = endDate.toLocaleDateString([], {
        ...dateOpts,
        timeZone: resolvedTimezone,
      });
      return `${dateLabel} -> ${endDateLabel}`;
    };

    return (
      <div className="agent-tasks-panel" aria-label="upcoming tasks">
        <div className="tasks-panel-header">
          <div className="tasks-panel-titles">
            <h3>upcoming tasks</h3>
            {selectedLabel && (
              <span className="tasks-panel-subtitle">starting {selectedLabel}</span>
            )}
          </div>
          <div className="tasks-panel-actions">
            <button
              className="event-btn"
              disabled={!backendReady}
              title="Refresh tasks"
              aria-label="Refresh tasks"
              onClick={() => {
                if (!backendReady) return;
                onRefreshCalendar?.();
              }}
            >
              Refresh
            </button>
            <button
              className="event-btn"
              disabled={!backendReady}
              title="Create a task"
              aria-label="Create a task"
              onClick={() => {
                if (!backendReady) return;
                const base =
                  state.selectedCalendarDate instanceof Date
                    ? new Date(state.selectedCalendarDate)
                    : new Date();
                setToolEditorState({
                  mode: "task",
                    taskPrefill: {
                      start_time: Math.floor(base.getTime() / 1000),
                      timezone: preferredTimezone,
                      status: "pending",
                    },
                    onSaveTask: persistTask,
                });
              }}
            >
              Plan task
            </button>
          </div>
        </div>
        {promptedReminders.length > 0 && (
          <div className="tasks-panel-reminders" role="status" aria-live="polite">
            {promptedReminders.map(({ event }, index) => (
              <article
                key={event.id || `${event.summary || "reminder"}-${index}`}
                className="tasks-panel-reminder"
              >
                <strong>{event.summary || event.title || "Reminder"}</strong>
                <p>{event.prompt_message || event.description || "Reminder is due."}</p>
              </article>
            ))}
          </div>
        )}
        <div className="tasks-panel-search">
          <input
            type="search"
            placeholder="Filter tasks..."
            value={taskQuery}
            onChange={(event) => setTaskQuery(event.target.value)}
          />
        </div>
        <ul className="task-card-list">
          {items.length === 0 ? (
            <li className="task-card-empty">{emptyMessage}</li>
          ) : (
            items.map(({ event, startDate, endDate }, index) => {
              const key =
                event.id ||
                event.event_id ||
                `${event.summary || "task"}-${startDate?.toISOString() || index}`;
               const windowLabel = formatWindow(
                 startDate,
                 endDate,
                 event.timezone || preferredTimezone,
               );
              const isoStart = startDate ? startDate.toISOString() : undefined;
              const statusInfo = taskStatusInfo(event, startDate, endDate);
              const statusKey = normalizeTaskStatusKey(event.status);
              const cardStatusClass = statusInfo?.className || "pending";
              const actions = Array.isArray(event.actions) ? event.actions : [];
              const normalizedActions = actions.filter(
                (action) => action && typeof action === "object" && !Array.isArray(action),
              );
              const parsedTool = (() => {
                const desc = event.description;
                if (typeof desc !== "string" || !desc.trim()) return null;
                try {
                  const parsed = JSON.parse(desc);
                  if (
                    parsed &&
                    typeof parsed === "object" &&
                    typeof parsed.tool === "string" &&
                    parsed.tool.trim() &&
                    parsed.args &&
                    typeof parsed.args === "object"
                  ) {
                    return { tool: parsed.tool.trim(), args: parsed.args };
                  }
                } catch {}
                return null;
              })();
              const resolvedActions =
                normalizedActions.length > 0
                  ? normalizedActions
                  : parsedTool
                    ? [{ kind: "tool", name: parsedTool.tool, args: parsedTool.args }]
                    : [];
              const hasActions = resolvedActions.length > 0;
              const actionPayloadText = (() => {
                if (!hasActions) return null;
                if (resolvedActions.length === 1) {
                  const action = resolvedActions[0];
                  const kind = String(action.kind || action.type || "").toLowerCase();
                  if (kind === "tool") {
                    const toolName = (action.name && String(action.name)) || "tool";
                    const toolArgs =
                      action.args && typeof action.args === "object" ? action.args : {};
                    return JSON.stringify({ tool: toolName, args: toolArgs }, null, 2);
                  }
                  if (kind === "prompt") {
                    const prompt = action.prompt ? String(action.prompt) : "";
                    return JSON.stringify({ prompt }, null, 2);
                  }
                }
                const summarized = resolvedActions.map((action) => {
                  const kind = String(action.kind || action.type || "").toLowerCase();
                  if (kind === "tool") {
                    const toolName = (action.name && String(action.name)) || "tool";
                    const toolArgs =
                      action.args && typeof action.args === "object" ? action.args : {};
                    const prompt = action.prompt ? String(action.prompt) : undefined;
                    return prompt
                      ? { kind, name: toolName, args: toolArgs, prompt }
                      : { kind, name: toolName, args: toolArgs };
                  }
                  if (kind === "prompt") {
                    const prompt = action.prompt ? String(action.prompt) : "";
                    return { kind, prompt };
                  }
                  return action;
                });
                return JSON.stringify(summarized, null, 2);
              })();
              return (
                <li key={key} className="task-card-item">
                  <article className={`task-card status-${cardStatusClass}`}>
                    <header className="task-card-header">
                      <time
                        className="task-card-when"
                        dateTime={isoStart}
                        aria-label={
                          startDate
                            ? `Scheduled ${windowLabel}`
                            : "Unscheduled task"
                        }
                      >
                        {windowLabel}
                      </time>
                      {statusInfo && (
                        <span
                          className={`task-card-status ${statusInfo.className}`}
                          title={statusInfo.title}
                        >
                          {statusInfo.label}
                        </span>
                      )}
                    </header>
                    <h4 className="task-card-title">
                      {event.summary || "Untitled task"}
                    </h4>
                    {hasActions && actionPayloadText ? (
                      <pre className="task-card-code" aria-label="Scheduled actions payload">
                        {actionPayloadText}
                      </pre>
                    ) : (
                      event.description && (
                        <p className="task-card-description">{event.description}</p>
                      )
                    )}
                    {event.location && (
                      <p className="task-card-location">{event.location}</p>
                    )}
                    <div className="task-card-actions">
                      {hasActions && (
                        <button
                          className="event-btn"
                          title="Run scheduled actions"
                          disabled={!backendReady || !event.id}
                          onClick={async () => {
                            if (!backendReady || !event.id) return;
                            try {
                              await axios.post(`/api/calendar/events/${event.id}/run`, null);
                              onRefreshCalendar?.();
                            } catch (err) {
                              console.error("Run actions failed", err);
                            }
                          }}
                        >
                          Run
                        </button>
                      )}
                      <button
                        className="event-btn"
                        title={
                          statusKey === "acknowledged" || statusKey === "skipped"
                            ? "View task details"
                            : "Review or update this task"
                        }
                        disabled={!backendReady || !event.id}
                        onClick={(evt) => {
                          evt.stopPropagation();
                          if (!backendReady || !event.id) return;
                          openTaskEditor({ task: event });
                        }}
                      >
                        {statusKey === "acknowledged" || statusKey === "skipped"
                          ? "View"
                          : "Review"}
                      </button>
                      <button
                        className="event-btn"
                        title="Delete"
                        disabled={!backendReady || !event.id}
                        onClick={async () => {
                          if (!backendReady || !event.id) return;
                          try {
                            await axios.delete(`/api/calendar/events/${event.id}`);
                            onRefreshCalendar?.();
                          } catch (err) {
                            console.error("Delete event failed", err);
                          }
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </article>
                </li>
              );
            })
          )}
        </ul>
      </div>
    );
  };

  const renderRuntimePanel = () => {
    const runtime = runtimeStatus;
    const modeLabel = state.backendMode || runtime?.mode || "api";
    if (modeLabel !== "local") {
      return null;
    }
    if (usingProviderRuntime && selectedLocalProvider) {
      const providerRuntime = providerStatus || runtime || {};
      const capabilities =
        providerRuntime?.capabilities && typeof providerRuntime.capabilities === "object"
          ? providerRuntime.capabilities
          : {};
      const installed = !!providerRuntime.installed;
      const serverRunning = !!providerRuntime.server_running;
      const modelLoaded = !!providerRuntime.model_loaded;
      const loadedModel =
        typeof providerRuntime.loaded_model === "string"
          ? providerRuntime.loaded_model.trim()
          : "";
      const providerModelOptions = Array.from(
        new Set(
          [...providerModels, loadedModel, providerSelectedModel]
            .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
            .filter(Boolean),
        ),
      );
      const effectiveSelectedModel =
        (providerSelectedModel || loadedModel || providerModelOptions[0] || "").trim();
      const contextSupported = capabilities.context_length !== false;
      const providerStatusLabel = modelLoaded
        ? "model loaded"
        : serverRunning
          ? "server running"
          : installed
            ? "installed"
            : "not installed";
      const providerLabel = formatLocalRuntimeLabel(selectedLocalProvider);
      const baseUrl =
        typeof providerRuntime.base_url === "string" ? providerRuntime.base_url : "";
      const contextLength =
        typeof providerRuntime.context_length === "number"
          ? providerRuntime.context_length
          : null;
      const runtimeLastError =
        providerActionError ||
        providerRuntime.last_error ||
        runtimeError ||
        "";
      const logsCount = Array.isArray(providerLogs) ? providerLogs.length : 0;
      const loadBusy = providerPendingAction === "load";
      const unloadBusy = providerPendingAction === "unload";
      const startBusy = providerPendingAction === "start";
      const stopBusy = providerPendingAction === "stop";
      const controlsLocked = loadBusy || unloadBusy;

      return (
        <section className="agent-runtime-panel">
          <header className="runtime-panel-header">
            <div className="runtime-panel-title">
              <h3>runtime</h3>
              <span className="runtime-panel-subtitle">{providerLabel || "local runtime"}</span>
            </div>
            <div className="runtime-panel-actions">
              <button
                type="button"
                className="runtime-action-btn"
                onClick={() => {
                  fetchRuntimeStatus();
                  fetchProviderStatus();
                  fetchProviderModels();
                  fetchProviderLogs({ reset: true });
                }}
                disabled={providerActionPending && providerPendingAction === "refresh"}
                aria-label="Refresh provider runtime status"
                title="Refresh provider runtime status"
              >
                refresh
              </button>
              <div className="runtime-panel-status" title={`runtime status: ${providerStatusLabel}`}>
                {providerStatusLabel}
              </div>
            </div>
          </header>
          <div className="runtime-model-row">
            <span className="runtime-model-name" title={providerLabel || selectedLocalProvider}>
              {providerLabel || selectedLocalProvider}
            </span>
            {baseUrl ? <span className="runtime-pill">{baseUrl}</span> : null}
            {contextLength ? (
              <span className="runtime-pill" title="loaded context length">
                ctx {formatTokenCount(contextLength)}
              </span>
            ) : null}
          </div>
          <div className="runtime-provider-actions">
            <button
              type="button"
              className="runtime-action-btn"
              onClick={handleProviderStart}
              disabled={startBusy || capabilities.start_stop === false}
              title={
                capabilities.start_stop === false
                  ? "Start is unavailable in remote-unmanaged mode."
                  : "Start provider server"
              }
            >
              {startBusy ? "starting..." : "start"}
            </button>
            <button
              type="button"
              className="runtime-action-btn"
              onClick={handleProviderStop}
              disabled={stopBusy || capabilities.start_stop === false}
              title={
                capabilities.start_stop === false
                  ? "Stop is unavailable in remote-unmanaged mode."
                  : "Stop provider server"
              }
            >
              {stopBusy ? "stopping..." : "stop"}
            </button>
            <select
              className="model-select runtime-provider-model-select"
              value={effectiveSelectedModel}
              onChange={(event) => setProviderSelectedModel(event.target.value)}
              disabled={controlsLocked}
              title="Provider model"
            >
              {providerModelOptions.length === 0 ? (
                <option value="">no provider models</option>
              ) : (
                providerModelOptions.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))
              )}
            </select>
            <button
              type="button"
              className="runtime-action-btn"
              onClick={handleProviderLoad}
              disabled={loadBusy || !effectiveSelectedModel}
              title="Load selected provider model"
            >
              {loadBusy ? "loading..." : "load"}
            </button>
            <button
              type="button"
              className="runtime-action-btn"
              onClick={handleProviderUnload}
              disabled={unloadBusy || (!effectiveSelectedModel && !loadedModel)}
              title="Unload provider model"
            >
              {unloadBusy ? "unloading..." : "unload"}
            </button>
          </div>
          <div className="runtime-provider-actions">
            <label className="runtime-context-label" htmlFor="runtime-provider-context">
              context
            </label>
            <input
              id="runtime-provider-context"
              className="runtime-provider-context-input"
              type="number"
              min="0"
              step="1"
              value={providerContextDraft}
              onChange={(event) => setProviderContextDraft(event.target.value)}
              disabled={controlsLocked || !contextSupported}
              placeholder={contextSupported ? "optional" : "unsupported"}
              title={
                contextSupported
                  ? "Optional context length for load requests"
                  : "Context length control is unavailable for this provider."
              }
            />
            <button
              type="button"
              className="runtime-action-btn"
              onClick={() => setProviderLogsOpen((prev) => !prev)}
              disabled={false}
              title="Show or hide provider runtime logs"
            >
              {providerLogsOpen ? "hide logs" : "show logs"} ({logsCount})
            </button>
          </div>
          {runtimeLastError ? (
            <div className="runtime-panel-error" role="status">
              {runtimeLastError}
            </div>
          ) : null}
          {providerLogsOpen ? (
            <div className="runtime-provider-logs-wrap">
              <pre className="runtime-provider-logs">
                {(providerLogs || [])
                  .slice(-120)
                  .map((entry) => {
                    const ts = entry?.time
                      ? new Date(entry.time * 1000).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })
                      : "";
                    const level = String(entry?.level || "info").toUpperCase();
                    const msg = String(entry?.message || "").trim();
                    return `${ts} ${level} ${msg}`.trim();
                  })
                  .join("\n") || "No logs yet."}
              </pre>
            </div>
          ) : null}
        </section>
      );
    }
    const modelName =
      runtime?.model || state.transformerModel || state.localModel || "";
    const loadState = runtime?.load_state || "idle";
    const isLoaded = runtime?.loaded || loadState === "ready";
    const loadError = runtime?.load_error || runtimeError;
    const hasModel = Boolean(modelName);
    const downloadState = modelVerify?.exists ? "done" : "pending";
    const verifyState = modelVerify?.verified
      ? "done"
      : modelVerify?.exists
        ? "pending"
        : "pending";
    const loadingState =
      loadState === "loading"
        ? "active"
        : loadState === "ready"
          ? "done"
          : loadState === "error"
            ? "error"
            : "pending";
    const readyState =
      loadState === "ready"
        ? "done"
        : loadState === "error"
          ? "error"
          : "pending";
    const gpuSnapshots = Array.isArray(runtime?.memory?.gpu)
      ? runtime.memory.gpu
      : [];
    const systemSnapshot =
      runtime?.memory && typeof runtime.memory === "object"
        ? runtime.memory.system
        : null;
    const systemTotal = systemSnapshot?.total_bytes;
    const systemAvailable = systemSnapshot?.available_bytes;
    const systemUsed =
      typeof systemSnapshot?.used_bytes === "number"
        ? systemSnapshot.used_bytes
        : typeof systemTotal === "number" && typeof systemAvailable === "number"
          ? systemTotal - systemAvailable
          : null;
    const modelBytes = (() => {
      const installed = modelVerify?.installed_bytes;
      if (typeof installed === "number" && installed > 0) return installed;
      const expected = modelVerify?.expected_bytes;
      if (typeof expected === "number" && expected > 0) return expected;
      return null;
    })();
    const modelSizeLabel = modelBytes ? formatBytes(modelBytes) : null;
    const gpuTotalBytes = gpuSnapshots.reduce((max, entry) => {
      const total = entry?.total_bytes;
      if (typeof total !== "number" || !Number.isFinite(total)) return max;
      return Math.max(max, total);
    }, 0);
    const ramSwapEnabled = runtime?.ram_swap_enabled === true;
    const needsRamSwapWarning =
      modeLabel === "local" &&
      modelBytes &&
      gpuTotalBytes &&
      modelBytes > gpuTotalBytes &&
      !ramSwapEnabled;
    const exceedsSystemWarning =
      modeLabel === "local" &&
      modelBytes &&
      gpuTotalBytes &&
      systemTotal &&
      modelBytes > gpuTotalBytes + systemTotal;
    const showProjectedRam =
      modeLabel === "local" && !isLoaded && !!modelSizeLabel;

    const stepItems = [
      { key: "downloaded", label: "files", state: downloadState },
      { key: "verified", label: "verified", state: verifyState },
      { key: "loading", label: "loading", state: loadingState },
      { key: "ready", label: "ready", state: readyState },
    ];
    const agentResource = (() => {
      const sessionId = state.sessionId;
      if (!sessionId) return null;
      const resourceMatch = (resourceSnapshot || []).find((item) => {
        const id = item?.agent_id || item?.session_id;
        return id && String(id) === String(sessionId);
      });
      if (resourceMatch) return resourceMatch;
      const match = (agents || []).find((agent) => {
        const id =
          agent?.id || agent?.agent_id || agent?.session_id || agent?.chain_id;
        return id && String(id) === String(sessionId);
      });
      if (match?.resources) return match.resources;
      const fallback = (agents || []).find((agent) => agent?.resources);
      return fallback?.resources || null;
    })();
    const tokenLimit =
      typeof state.maxContextLength === "number" && state.maxContextLength > 0
        ? state.maxContextLength
        : null;
    const tokenPrompt =
      agentResource?.last_prompt_tokens ?? agentResource?.prompt_tokens_total ?? null;
    const tokenCompletion =
      agentResource?.last_completion_tokens ?? agentResource?.completion_tokens_total ?? null;
    const tokenTotal =
      agentResource?.last_total_tokens ?? agentResource?.total_tokens ?? null;
    const tokenSource = agentResource?.last_source || agentResource?.source || null;
    const tokenRatio =
      typeof tokenTotal === "number" && tokenLimit
        ? Math.min(1, Math.max(0, tokenTotal / tokenLimit))
        : 0;
    const currentContextLength = parseContextLength(state.maxContextLength);
    const draftContextLength = parseContextLength(contextDraft);
    const normalizedDraft = snapContextLength(draftContextLength);
    const canApplyContext =
      !!normalizedDraft &&
      normalizedDraft !== currentContextLength &&
      !contextSaving &&
      backendReady;
    const contextButtonLabel = contextSaving ? "saving..." : "apply";
    const sliderPercent =
      sliderRange.max > sliderRange.min
        ? ((sliderRange.value - sliderRange.min) /
            (sliderRange.max - sliderRange.min)) *
          100
        : 0;
    const contextValueLabel =
      typeof sliderRange.value === "number" && Number.isFinite(sliderRange.value)
        ? sliderRange.value.toLocaleString()
        : "n/a";
    const contextEstimateLabel = contextEstimateLoading
      ? "estimating..."
      : formatEstimate(contextEstimateMb);
    const showContextEstimate = backendReady;

    const renderMeter = (label, used, total, meta, tooltip) => {
      const ratio =
        typeof used === "number" && typeof total === "number" && total > 0
          ? Math.min(1, Math.max(0, used / total))
          : 0;
      return (
        <div className="runtime-meter" title={tooltip || meta || undefined}>
          <div className="runtime-meter-row">
            <span className="runtime-meter-label">{label}</span>
            <span className="runtime-meter-value">
              {typeof used === "number" && typeof total === "number"
                ? `${formatBytes(used)} / ${formatBytes(total)}`
                : "n/a"}
            </span>
          </div>
          <div className="runtime-meter-bar" aria-hidden="true">
            <div
              className="runtime-meter-fill"
              style={{ width: `${(ratio * 100).toFixed(1)}%` }}
            />
          </div>
          {meta ? <div className="runtime-meter-meta">{meta}</div> : null}
        </div>
      );
    };

    const renderGpuMeters = () => {
      if (!gpuSnapshots.length) {
        return renderMeter("GPU", null, null, "No GPU telemetry");
      }
      return gpuSnapshots.slice(0, 2).map((gpu, idx) => {
        const used = gpu?.used_bytes;
        const total = gpu?.total_bytes;
        const parts = [];
        if (gpu?.allocated_bytes) {
          parts.push(`alloc ${formatBytes(gpu.allocated_bytes)}`);
        }
        if (gpu?.reserved_bytes) {
          parts.push(`reserved ${formatBytes(gpu.reserved_bytes)}`);
        }
        const label = gpu?.name
          ? `${gpu.name}`
          : `GPU ${gpu?.index ?? idx}`;
        const meta = parts.join(" · ");
        return (
          <div key={gpu?.id || idx} className="runtime-meter-block">
            {renderMeter(label, used, total, meta, meta)}
          </div>
        );
      });
    };

    const statusText = !backendReady
      ? "offline"
      : runtimeLoading
        ? "updating..."
        : runtimeError
          ? "offline"
          : "live";

    const actionPending = loadPending || unloadPending;
    const actionLabel = isLoaded
      ? unloadPending
        ? "unloading..."
        : "unload"
      : loadPending
        ? "loading..."
        : "load";
    const actionTitle = isLoaded
      ? "Unload the local model from VRAM"
      : "Load the selected local model into VRAM";
    const tokenMetaParts = [];
    if (typeof tokenPrompt === "number") {
      tokenMetaParts.push(`in ${formatTokenCount(tokenPrompt)}`);
    }
    if (typeof tokenCompletion === "number") {
      tokenMetaParts.push(`out ${formatTokenCount(tokenCompletion)}`);
    }
    if (typeof tokenTotal === "number") {
      tokenMetaParts.push(`total ${formatTokenCount(tokenTotal)}`);
    }
    if (tokenLimit) {
      tokenMetaParts.push(`limit ${formatTokenCount(tokenLimit)}`);
    }
    if (tokenSource) {
      tokenMetaParts.push(tokenSource);
    }
    const tokenMeta = tokenMetaParts.join(" · ");
    const tokenValue =
      typeof tokenTotal === "number"
        ? tokenLimit
          ? `${formatTokenCount(tokenTotal)} / ${formatTokenCount(tokenLimit)}`
          : `${formatTokenCount(tokenTotal)}`
        : "n/a";

    return (
      <section className="agent-runtime-panel">
        <header className="runtime-panel-header">
          <div className="runtime-panel-title">
            <h3>runtime</h3>
            <span className="runtime-panel-subtitle">
              {modeLabel === "local"
                ? "local inference"
                : `mode: ${modeLabel}`}
            </span>
          </div>
          <div className="runtime-panel-actions">
            {modeLabel === "local" && (
              <span className="runtime-action-wrap" title={actionTitle}>
                <button
                  type="button"
                  className="runtime-action-btn"
                  onClick={isLoaded ? handleUnloadLocalModel : handleLoadLocalModel}
                  disabled={actionPending}
                >
                  {actionLabel}
                </button>
              </span>
            )}
            <div className="runtime-panel-status" title={`runtime status: ${statusText}`}>
              {statusText}
            </div>
          </div>
        </header>
        <div className="runtime-model-row">
          <span
            className="runtime-model-name"
            title={hasModel ? `local model: ${modelName}` : "no local model selected"}
          >
            {hasModel ? modelName : "local model"}
          </span>
          {tokenLimit && (
            <span className="runtime-pill" title="max context length">
              ctx {formatTokenCount(tokenLimit)}
            </span>
          )}
          {runtime?.quant_method && (
            <span className="runtime-pill" title="quantization method">
              {runtime.quant_method}
            </span>
          )}
          {runtime?.model_dtype && (
            <span className="runtime-pill" title="model dtype">
              dtype {runtime.model_dtype}
            </span>
          )}
          {runtime?.model_device && (
            <span className="runtime-pill" title="model device">
              {runtime.model_device}
            </span>
          )}
        </div>
        <div className="runtime-context-row" ref={contextWrapRef}>
          <span className="runtime-context-label">Context</span>
          <div
            className={`runtime-context-slider${backendReady ? "" : " is-disabled"}`}
            ref={contextSliderRef}
            onPointerDown={handleContextPointerDown}
            onPointerMove={handleContextPointerMove}
            onPointerUp={handleContextPointerUp}
            onPointerCancel={handleContextPointerUp}
            role="presentation"
          >
            <div className="runtime-context-track" />
            <div
              className="runtime-context-fill"
              style={{ width: `${sliderPercent.toFixed(1)}%` }}
            />
            <button
              type="button"
              className="runtime-context-handle"
              style={{ left: `${sliderPercent.toFixed(1)}%` }}
              role="slider"
              aria-label="Max context length"
              aria-valuemin={sliderRange.min}
              aria-valuemax={sliderRange.max}
              aria-valuenow={sliderRange.value}
              aria-valuetext={contextValueLabel}
              onClick={() => {
                if (!backendReady) return;
                setContextPopupOpen(true);
                setContextEditing(false);
              }}
              onKeyDown={handleContextKeyDown}
              disabled={!backendReady}
            >
              <span className="runtime-context-dot" aria-hidden="true" />
            </button>
            {contextPopupOpen && (
              <div
                className="runtime-context-popup"
                style={{ left: `${sliderPercent.toFixed(1)}%` }}
              >
                {contextEditing ? (
                  <input
                    ref={contextInputRef}
                    className="runtime-context-popup-input"
                    type="number"
                    inputMode="numeric"
                    min={MIN_CONTEXT_LENGTH}
                    step={CONTEXT_STEP}
                    value={contextDraft}
                    onChange={(event) => {
                      setContextDraft(event.target.value);
                      setContextDirty(true);
                      setContextError("");
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        applyContextLength();
                        setContextEditing(false);
                      } else if (event.key === "Escape") {
                        event.preventDefault();
                        setContextEditing(false);
                      }
                    }}
                    aria-label="Edit max context length"
                  />
                ) : (
                  <button
                    type="button"
                    className="runtime-context-popup-value"
                    onClick={() => setContextEditing(true)}
                  >
                    {contextValueLabel}
                  </button>
                )}
                <span className="runtime-context-popup-unit">tokens</span>
              </div>
            )}
          </div>
          <button
            type="button"
            className="runtime-action-btn runtime-context-apply"
            onClick={applyContextLength}
            disabled={!canApplyContext}
            aria-label="Apply max context length"
          >
            {contextButtonLabel}
          </button>
        </div>
        {showContextEstimate && (
          <div className="runtime-context-estimate">
            <span className="runtime-context-estimate-label">Projected VRAM</span>
            <span
              className="runtime-context-estimate-value"
              title={contextEstimateError || undefined}
            >
              {contextEstimateLabel}
            </span>
          </div>
        )}
        <ol className="runtime-stepper" aria-label="local model status">
          {stepItems.map((step) => (
            <li
              key={step.key}
              className="runtime-step"
              data-state={step.state}
              title={`${step.label}: ${step.state}`}
            >
              <span className="runtime-step-dot" aria-hidden="true" />
              <span className="runtime-step-label">{step.label}</span>
            </li>
          ))}
        </ol>
        {loadError && (
          <div className="runtime-panel-error" role="status">
            {loadError}
          </div>
        )}
        {modelVerifyError && (
          <div className="runtime-panel-error" role="status">
            {modelVerifyError}
          </div>
        )}
        {unloadError && (
          <div className="runtime-panel-error" role="status">
            {unloadError}
          </div>
        )}
        {contextError && (
          <div className="runtime-panel-error" role="status">
            {contextError}
          </div>
        )}
        {exceedsSystemWarning && (
          <div className="runtime-panel-warning" role="status">
            Model size {modelSizeLabel || ""} exceeds GPU + RAM capacity. Offload may fail.
          </div>
        )}
        {needsRamSwapWarning && (
          <div className="runtime-panel-warning" role="status">
            Model size {modelSizeLabel || ""} exceeds GPU VRAM. Enable RAM swap to offload weights.
          </div>
        )}
        {showProjectedRam && (
          <div className="runtime-panel-note" role="status">
            Projected RAM if fully offloaded: {modelSizeLabel}
          </div>
        )}
        <div className="runtime-meters">
          {renderGpuMeters()}
          {renderMeter("RAM", systemUsed, systemTotal, null, "System RAM usage")}
          <div className="runtime-meter" title={tokenMeta || "Token usage"}>
            <div className="runtime-meter-row">
              <span className="runtime-meter-label">Tokens</span>
              <span className="runtime-meter-value">{tokenValue}</span>
            </div>
            <div className="runtime-meter-bar" aria-hidden="true">
              <div
                className="runtime-meter-fill"
                style={{ width: `${(tokenRatio * 100).toFixed(1)}%` }}
              />
            </div>
            <div className="runtime-meter-meta">
              {tokenMeta || "No token telemetry yet"}
            </div>
          </div>
        </div>
      </section>
    );
  };

  return (
    <>
      <aside
        ref={sidebarRef}
        className={`sidebar right-sidebar${collapsed ? " collapsed" : ""}`}
      >
      <button
        className="collapse-btn"
        onClick={(event) =>
          handleUnifiedPress(event, () => {
            const btn = event.currentTarget;
            if (btn.__hoverTimer) {
              clearTimeout(btn.__hoverTimer);
              btn.__hoverTimer = null;
            }
            if (btn.__lastHoverToggleAt && Date.now() - btn.__lastHoverToggleAt < 300) {
              return;
            }
            onToggle?.();
          })
        }
        onPointerDown={(event) =>
          handleUnifiedPress(event, () => {
            const btn = event.currentTarget;
            if (btn.__hoverTimer) {
              clearTimeout(btn.__hoverTimer);
              btn.__hoverTimer = null;
            }
            if (btn.__lastHoverToggleAt && Date.now() - btn.__lastHoverToggleAt < 300) {
              return;
            }
            onToggle?.();
          })
        }
        onMouseEnter={(event) => {
          if (!supportsHoverInteractions()) return;
          const btn = event.currentTarget;
          if (btn.__hoverTimer) clearTimeout(btn.__hoverTimer);
          btn.__hoverTimer = setTimeout(() => {
            btn.__lastHoverToggleAt = Date.now();
            onToggle?.();
          }, 1000);
        }}
        onMouseLeave={(event) => {
          const btn = event.currentTarget;
          if (btn.__hoverTimer) {
            clearTimeout(btn.__hoverTimer);
            btn.__hoverTimer = null;
          }
        }}
        aria-label="Collapse agent console"
        title="Collapse agent console"
      >
        {">"}
      </button>
      <div className="sidebar-header right-header">
        <button
          className={`stream-toggle ${streamEnabled ? "on" : "off"}`}
          onClick={onStreamToggle}
          title={streamEnabled ? "Pause console stream" : "Resume console stream"}
          aria-pressed={streamEnabled}
        >
          {streamEnabled ? "pause" : "resume"}
        </button>
        <h2>agent console</h2>
        <div className="console-permission-control">
          <label htmlFor="console-permission-select">permissions</label>
          <select
            id="console-permission-select"
            className="console-permission-select"
            value={state.approvalLevel}
            onChange={handleApprovalLevelChange}
            title="Select tool permissions level"
            aria-label="Tool permissions level"
          >
            <option value="all">All</option>
            <option value="high">High Risk Only</option>
            <option value="auto">Full Auto</option>
          </select>
        </div>
        {hiddenCount > 0 && (
          <button
            type="button"
            className="console-hidden-btn"
            onClick={handleShowHidden}
            title="Show hidden console cards"
            aria-label="Show hidden console cards"
          >
            show hidden ({hiddenCount})
          </button>
        )}
        <button
          className="refresh-btn"
          disabled={refreshDisabled}
          onClick={handleRefreshClick}
          aria-label="Refresh agent console"
          title="Refresh"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M17.65 6.35A7.95 7.95 0 0 0 12 4V1L7 6l5 5V7a5 5 0 1 1-4.9 6.1H5.02A7 7 0 1 0 17.65 6.35z" />
          </svg>
        </button>
      </div>
      <div className="agent-console-body" ref={scrollBodyRef}>
        {renderRuntimePanel()}
        {renderSyncInbox()}
        {showStandaloneActionHistory && !actionHistoryHidden ? (
          <ActionHistoryPanel
            actions={actions}
            backendReady={backendReady}
            onRefresh={onRefreshAgents}
            collapsed={actionHistoryCollapsed}
            onToggleCollapsed={() => setActionHistoryCollapsed((prev) => !prev)}
            onHide={() => setActionHistoryHidden(true)}
          />
        ) : null}
        {isCalendar && renderCalendar()}
        {hasInlineToolActivity && (
          <p className="agent-console-note" role="status">
            Tool details are inline in chat. The console is showing thoughts, messages, and tasks
            only.
          </p>
        )}
        {backendReady ? (
          agents.length === 0 ? (
            <p className="agent-console-empty">
              {loadingSnapshot ? "Loading agents..." : "No active agents yet."}
            </p>
          ) : (
            agents.map((agent) => renderAgentCard(agent))
          )
        ) : (
          <p className="agent-console-empty">Console unavailable while API is offline.</p>
        )}
      </div>
      <div
        className={`sidebar-resizer${isResizing ? " is-resizing" : ""}`}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize agent console"
        title="Drag to resize. Shift + Arrow keys resize faster. Home resets width."
        onPointerDown={startResize}
        onDoubleClick={resetSidebarWidth}
        onKeyDown={handleResizeKeyDown}
        tabIndex={0}
      />
    </aside>
    {toolEditorState && (
      <ToolEditorModal
        open
        tool={toolEditorState.tool}
        schedulePrefill={toolEditorState.schedulePrefill}
        mode={toolEditorState.mode || "tool"}
        task={toolEditorState.task}
        taskPrefill={toolEditorState.taskPrefill}
        onSaveTask={toolEditorState.onSaveTask}
        onCancel={() => setToolEditorState(null)}
        onSubmit={
          toolEditorState.onSubmit
            ? async ({ args, name, continueTarget }) => {
                try {
                  await toolEditorState.onSubmit?.({ args, name, continueTarget });
                } finally {
                  setToolEditorState(null);
                }
              }
            : undefined
        }
        onSchedule={toolEditorState.onSchedule}
      />
    )}
    {renderBrowserSessionPopup()}
    </>
  );
};

export default AgentConsole;

