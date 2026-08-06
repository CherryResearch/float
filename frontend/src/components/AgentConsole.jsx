import React from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { GlobalContext } from "../main";
import "../styles/Sidebar.css";
import "../styles/ToolActions.css";
import "../styles/ToolPayload.css";
import ActionHistoryPanel from "./ActionHistoryPanel";
import BrowserSessionDialog from "./BrowserSessionDialog";
import ConsoleObjectCard from "./ConsoleObjectCard";
import ToolEditorModal from "./ToolEditorModal";
import ToolPayloadView, {
  extractComputerPayload,
  summarizeToolPayload,
} from "./ToolPayloadView";
import StateInspector from "./StateInspector";
import {
  formatLocalRuntimeLabel,
  isLocalRuntimeEntry,
  normalizeModelId,
  resolveLocalCatalogModelId,
  resolveRequestModelForMode,
  resolveRuntimeModelLabel,
} from "../utils/modelUtils";
import {
  filterChatCapableProviderModels,
  formatProviderLastOperation,
  isChatCapableProviderModelName,
} from "../utils/providerRuntime";
import {
  RUNTIME_AVAILABILITY,
  RUNTIME_PANEL_LANES,
  resolveRuntimePanelContract,
} from "../utils/runtimePanelContract";
import { buildProviderRuntimeInspectorRows } from "../utils/stateExplanations";
import {
  acquireToolContinuationLock,
  buildToolContinuationLockKey,
  buildToolContinuationSignature,
  hasMatchingToolContinuationSignature,
  releaseToolContinuationLock,
} from "../utils/toolContinuations";
import { handleUnifiedPress } from "../utils/pointerInteractions";
import { thinkingPayloadForMode } from "../utils/reasoningEffort";
import { outputTokenPayload } from "../utils/generationLimits";
import {
  normalizeToolDisplayMode,
  toolDisplayShowsConsole,
} from "../utils/toolDisplayModes";
import {
  TOOL_REVIEW_ACTION_EVENT,
  normalizeToolReviewAction,
  normalizeToolReviewTarget,
  toolReviewScopeSelectors,
} from "../utils/toolReviewActions";
import {
  appendToolContinuationPhase,
  mergeContinuationText,
} from "../utils/continuationText";

const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 760;
const SIDEBAR_VIEWPORT_GUTTER = 96;
const SIDEBAR_KEYBOARD_STEP = 20;
const SIDEBAR_KEYBOARD_STEP_FAST = 40;
const LOCAL_RUNTIME_POLL_MS = 8000;
const PROVIDER_RUNTIME_POLL_MS = 60000;
const SERVER_RUNTIME_POLL_MS = 60000;
const EMPTY_GLOBAL_STATE = Object.freeze({});
const NOOP_SET_STATE = () => {};
const RUNTIME_RAG_OPERATION_EVENT = "float:runtime-rag-operation";
const RUNTIME_RAG_OPERATION_CLEAR_MS = 9000;
const RUNTIME_RAG_OPERATION_STALE_CLEAR_MS = 45000;
const CLIENT_RESOLUTION_TOOLS = new Set(["camera.capture"]);
const RETRIABLE_TOOL_STATUSES = new Set([
  "error",
  "failed",
  "denied",
  "timeout",
  "cancelled",
  "canceled",
]);
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
const TOKEN_ESTIMATE_KEYS = [
  "text",
  "content",
  "message",
  "summary",
  "title",
  "name",
  "status",
  "error",
];
const TOKEN_ESTIMATE_MAX_PART_CHARS = 4000;
const TOKEN_ESTIMATE_MAX_MESSAGE_CHARS = 16000;

const appendTokenEstimatePart = (parts, value) => {
  if (value === null || value === undefined) return;
  if (typeof value === "string") {
    const text = value.replace(/\s+/g, " ").trim();
    if (text) parts.push(text.slice(0, TOKEN_ESTIMATE_MAX_PART_CHARS));
    return;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    parts.push(String(value));
  }
};

const collectTokenEstimateText = (value, parts, depth = 0) => {
  if (value === null || value === undefined || depth > 2) return;
  if (typeof value !== "object") {
    appendTokenEstimatePart(parts, value);
    return;
  }
  if (Array.isArray(value)) {
    value.slice(0, 12).forEach((entry) =>
      collectTokenEstimateText(entry, parts, depth + 1),
    );
    return;
  }
  TOKEN_ESTIMATE_KEYS.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      collectTokenEstimateText(value[key], parts, depth + 1);
    }
  });
};

const estimateTextTokens = (text) => {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return 0;
  return Math.max(1, Math.ceil(normalized.length / 4));
};

const estimateMessageTokens = (message) => {
  if (!message || typeof message !== "object") return 0;
  const parts = [];
  appendTokenEstimatePart(parts, message.role);
  TOKEN_ESTIMATE_KEYS.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(message, key)) {
      collectTokenEstimateText(message[key], parts);
    }
  });
  if (Array.isArray(message.tools)) {
    message.tools.slice(0, 12).forEach((tool) =>
      collectTokenEstimateText(tool, parts, 1),
    );
  }
  const text = parts.join("\n").slice(0, TOKEN_ESTIMATE_MAX_MESSAGE_CHARS);
  return estimateTextTokens(text) + 6;
};

const estimateConversationTokens = (messages) => {
  if (!Array.isArray(messages) || !messages.length) return 0;
  return messages.reduce((total, message) => total + estimateMessageTokens(message), 0);
};

const parseOperationTimestamp = (value) => {
  const text = String(value || "").trim();
  if (!text) return null;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : null;
};

const formatOperationElapsed = (ms) => {
  const normalizedMs = Math.max(0, Number(ms) || 0);
  if (normalizedMs < 1000) return `${Math.round(normalizedMs)} ms`;
  if (normalizedMs < 10_000) return `${(normalizedMs / 1000).toFixed(1)} s`;
  const totalSeconds = Math.floor(normalizedMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
};

const normalizeRuntimeRagOperation = (payload) => {
  if (!payload || typeof payload !== "object") return null;
  const data = payload.data && typeof payload.data === "object" ? payload.data : {};
  const operationId = String(data.operation_id || "").trim();
  if (!operationId.toLowerCase().startsWith("rag-query:")) return null;
  const kind = String(data.kind || "").trim().toLowerCase();
  if (kind !== "rag_query") return null;
  return {
    id: operationId,
    title: String(payload.title || "Retrieving chat context").trim(),
    body: String(payload.body || "").trim(),
    status: String(data.status || "running").trim().toLowerCase() || "running",
    phaseLabel: String(data.phase_label || "").trim(),
    detail: String(data.detail || "").trim(),
    phaseIndex: Number(data.phase_index),
    phaseCount: Number(data.phase_count),
    startedAtMs: parseOperationTimestamp(data.started_at),
    elapsedMs: Number(data.elapsed_ms),
    counts: data.counts && typeof data.counts === "object" ? data.counts : null,
    updatedAtMs: Date.now(),
  };
};

const readMessageMetadata = (message) =>
  message && typeof message.metadata === "object" && message.metadata
    ? message.metadata
    : null;

const summarizeConversationCompactions = (messages) => {
  if (!Array.isArray(messages) || !messages.length) {
    return { count: 0, summaryMessages: 0, markerMessages: 0, sourceMessages: null };
  }
  let summaryMessages = 0;
  let markerMessages = 0;
  let priorCarried = 0;
  let sourceMessages = null;
  messages.forEach((message) => {
    const metadata = readMessageMetadata(message);
    const compaction =
      metadata && typeof metadata.conversation_compaction === "object"
        ? metadata.conversation_compaction
        : null;
    if (compaction) {
      summaryMessages += 1;
      const prior = Number(compaction.prior_compaction_summaries_carried);
      if (Number.isFinite(prior) && prior > priorCarried) priorCarried = prior;
      const totalMessages = Number(compaction.total_messages);
      if (Number.isFinite(totalMessages) && totalMessages > 0) {
        sourceMessages = Math.max(sourceMessages || 0, totalMessages);
      }
    }
    if (
      metadata &&
      metadata.context_compaction_marker &&
      typeof metadata.context_compaction_marker === "object"
    ) {
      markerMessages += 1;
    }
  });
  const count = Math.max(markerMessages, summaryMessages + priorCarried);
  return { count, summaryMessages, markerMessages, sourceMessages };
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
      return { label: "active", hue: "var(--color-mint-green)" };
    case "running":
      return { label: "running", hue: "var(--color-mint-green)" };
    case "streaming":
      return { label: "streaming", hue: "var(--color-mint-green)" };
    case "proposed":
      return { label: "proposed", hue: "var(--color-lavender)" };
    case "waiting":
    case "pending":
    case "queued":
      return { label: "pending", hue: "var(--color-lavender)" };
    case "stop_requested":
      return { label: "stop requested", hue: "var(--color-warning, #b7791f)" };
    case "invoked":
    case "ok":
    case "success":
    case "complete":
      return {
        label: key === "invoked" ? "invoked" : "complete",
        hue: "var(--color-mint-green)",
      };
    case "scheduled":
      return { label: "scheduled", hue: "var(--color-primary)" };
    case "denied":
    case "error":
    case "failed":
    case "timeout":
      return { label: "error", hue: "var(--color-error)" };
    case "cancelled":
    case "canceled":
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

const RUNTIME_LANE_LABELS = Object.freeze({
  [RUNTIME_PANEL_LANES.CLOUD_API]: "Cloud API",
  [RUNTIME_PANEL_LANES.SERVER_LAN]: "Server/LAN",
  [RUNTIME_PANEL_LANES.LOCAL_PROVIDER]: "Local provider",
  [RUNTIME_PANEL_LANES.DIRECT_LOCAL]: "Direct local",
});

const runtimeAvailabilityTone = (availability) => {
  if (availability === RUNTIME_AVAILABILITY.USABLE) return "connected";
  if (availability === RUNTIME_AVAILABILITY.DEGRADED) return "warn";
  if (availability === RUNTIME_AVAILABILITY.UNAVAILABLE) return "error";
  return "loading";
};

const resolveToolDisplayName = (tool, fallback = "tool") => {
  if (!tool || typeof tool !== "object") return fallback;
  const candidates = [
    tool.name,
    tool.tool,
    tool.tool_name,
    tool.function?.name,
    tool.call?.name,
  ];
  const name = candidates
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .find(Boolean);
  return name || fallback;
};

const toolRequestId = (tool) => String(tool?.id ?? tool?.request_id ?? "").trim();

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

const normalizeRuntimeTimestamp = (value) => {
  if (value == null || value === "") return null;
  const raw = Number(value);
  if (Number.isFinite(raw)) {
    return raw > 0 && raw < 1e12 ? raw * 1000 : raw;
  }
  const parsed = new Date(value);
  const ms = parsed.getTime();
  return Number.isNaN(ms) ? null : ms;
};

const COMPACT_CONSOLE_PIP_LABELS = {
  API: "api",
  Background: "bg",
  Context: "ctx",
  Device: "dev",
  Lane: "lane",
  Model: "model",
  Operation: "op",
  Budget: "ctx",
  Retrieval: "rag",
  Loaded: "loaded",
  Provider: "prv",
  Runtime: "run",
  Status: "st",
  Transformer: "tf",
  WebSocket: "ws",
  background: "bg",
  model: "mdl",
  provider: "prv",
  runtime: "run",
  status: "st",
  websocket: "ws",
};

const compactConsolePipLabel = (label) => {
  const normalized = typeof label === "string" ? label.trim() : "";
  if (!normalized) return "";
  return COMPACT_CONSOLE_PIP_LABELS[normalized] || normalized;
};

const compactConsolePipValue = (value, label = "") => {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!normalized) return "";
  const normalizedLabel = typeof label === "string" ? label.trim().toLowerCase() : "";
  if (normalizedLabel === "lane") {
    const laneValues = {
      "cloud api": "Cloud",
      "server/lan": "Server",
      "local provider": "Provider",
      "direct local": "Local",
    };
    return laneValues[normalized.toLowerCase()] || normalized;
  }
  const compactValue = ["model", "provider"].includes(normalizedLabel)
    ? normalized.split(/[\\/]/).filter(Boolean).at(-1) || normalized
    : normalized;
  const lower = compactValue.toLowerCase();
  if (lower === "connected") return "on";
  if (lower === "disconnected" || lower === "offline") return "off";
  if (lower === "stopped") return "off";
  if (lower === "api runtime") return "api";
  if (lower === "api mode") return "api";
  if (lower === "local runtime") return "local";
  if (lower === "transformer model not selected") return "none";
  if (lower === "local model") return "local";
  if (lower === "0 task updates") return "0";
  const versionedOpenAiModel = compactValue.match(
    /^(gpt-\d+(?:\.\d+)?(?:-[a-z]+)?)-\d{4}-\d{2}-\d{2}$/i,
  );
  if (versionedOpenAiModel) return versionedOpenAiModel[1];
  return compactValue;
};

const formatRuntimeClockTime = (value) => {
  const normalized = normalizeRuntimeTimestamp(value);
  if (!normalized) return null;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const formatRuntimeRelativeTime = (value, now = Date.now()) => {
  const normalized = normalizeRuntimeTimestamp(value);
  if (!normalized) return null;
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

const normalizePreviewText = (value) => {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
};

const normalizeStatusValue = (value) => {
  if (typeof value === "string" && value.trim()) return value.trim().toLowerCase();
  if (value === true) return "online";
  if (value === false) return "offline";
  return "unknown";
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

const resolveTerminalStreamState = (entry, message = null) => {
  if (!entry || entry.type !== "stream") return null;
  const entryStatus = normalizeStatusValue(entry.status);
  const metadata =
    message?.metadata && typeof message.metadata === "object" ? message.metadata : {};
  const messageStatus = normalizeStatusValue(metadata.status);
  const terminalStatus = [entryStatus, messageStatus].find(
    (status) => status === "error" || status === "timeout",
  );
  if (terminalStatus === "error") {
    return {
      status: "error",
      summary: "response ended with an error",
      retry: "Retry from chat.",
    };
  }
  if (terminalStatus === "timeout") {
    return {
      status: "timeout",
      summary: "response timed out",
      retry: "Retry from chat.",
    };
  }
  if (metadata.unresolved_tool_loop) {
    return {
      status: "partial",
      summary: "tool follow-up stopped",
      retry: "Retry continuation from chat.",
    };
  }
  if (messageStatus === "partial") {
    return {
      status: "partial",
      summary: "partial response",
      retry: "Retry from chat.",
    };
  }
  return null;
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

const formatWorkflowMeta = (workflow) => {
  if (!workflow || typeof workflow !== "object") return "";
  const label =
    typeof workflow.label === "string" && workflow.label.trim()
      ? workflow.label.trim()
      : typeof workflow.id === "string"
        ? workflow.id.trim()
        : "";
  const role =
    typeof workflow.role === "string" && workflow.role.trim()
      ? workflow.role.trim()
      : "";
  if (label && role) return `${label} · ${role}`;
  return label || role;
};

const formatProvenanceMeta = (provenance) => {
  if (!provenance || typeof provenance !== "object") return "";
  const kind =
    typeof provenance.kind === "string" ? provenance.kind.trim().toLowerCase() : "";
  const parentSession =
    typeof provenance.parent_session_id === "string"
      ? provenance.parent_session_id.trim()
      : "";
  const parentMessage =
    typeof provenance.parent_message_id === "string"
      ? provenance.parent_message_id.trim()
      : "";
  const taskId =
    typeof provenance.task_id === "string" ? provenance.task_id.trim() : "";
  if (kind === "fork" && parentSession) {
    return `fork of ${parentSession}${parentMessage ? ` from ${parentMessage}` : ""}`;
  }
  if (kind === "subchat" && parentSession) {
    return `subchat from ${parentSession}${parentMessage ? ` / ${parentMessage}` : ""}`;
  }
  if (kind === "task_chain" && taskId) {
    return `task chain ${taskId}`;
  }
  if (kind && parentSession) {
    return `${kind} from ${parentSession}`;
  }
  return kind || "";
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const looksLikeUuid = (value) => UUID_PATTERN.test(String(value || "").trim());

const shortOpaqueId = (value, length = 8) => {
  const normalized = String(value || "").trim();
  return normalized ? normalized.slice(0, length) : "";
};

const AGENT_CARD_INTERACTIVE_SELECTOR =
  'button, a, input, select, textarea, label, summary, [role="button"], [data-no-card-toggle="true"]';

const getRenderableAgentActivity = (agent, { showToolEntries = true } = {}) => {
  const activity = Array.isArray(agent?.events) ? agent.events : [];
  return activity.filter(
    (entry) =>
      entry &&
      entry.type !== "content" &&
      (showToolEntries || entry.type !== "tool"),
  );
};

const agentHasUsefulDetails = (agent) => {
  if (!agent || typeof agent !== "object") return false;
  if (normalizePreviewText(agent.summary)) return true;
  if (agent.resources && Object.keys(agent.resources).length) return true;
  if (agent.workflow && Object.keys(agent.workflow).length) return true;
  if (agent.provenance && Object.keys(agent.provenance).length) return true;
  if (agent.handoff && Object.keys(agent.handoff).length) return true;
  if (agent.controls && Object.keys(agent.controls).length) return true;
  return false;
};

const isOpaqueEmptyAgentRecord = (agent, { showToolEntries = true } = {}) => {
  const id = String(agent?.id || agent?.agent_id || agent?.session_id || "").trim();
  const label = String(agent?.label || agent?.agent_label || agent?.name || agent?.title || "").trim();
  if (!looksLikeUuid(id) && !looksLikeUuid(label)) return false;
  return !getRenderableAgentActivity(agent, { showToolEntries }).length && !agentHasUsefulDetails(agent);
};

const shouldShowAgentInConsole = (agent, { showToolEntries = true } = {}) => {
  const id = String(agent?.id || "").trim();
  if (!id || id === "system:celery" || id === "system:background-autonomy") {
    return false;
  }
  if (isOpaqueEmptyAgentRecord(agent, { showToolEntries })) return false;
  return true;
};

const isBackgroundWorkAgent = (agent) => {
  if (!agent || typeof agent !== "object") return false;
  const id = String(agent.id || agent.agent_id || "").trim().toLowerCase();
  if (id.startsWith("task:") || id.startsWith("background:") || id.startsWith("reflection:")) {
    return true;
  }
  const provenance =
    agent.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
  const kind = String(provenance?.kind || agent.kind || "").trim().toLowerCase();
  return (
    kind === "task_chain" ||
    kind === "scheduled_prompt" ||
    kind === "background" ||
    kind === "background_reflection"
  );
};

const isActiveRunStatus = (value) =>
  ["active", "running", "streaming", "stop_requested"].includes(
    String(value || "").trim().toLowerCase(),
  );

const isReflectionAgent = (agent) => {
  if (!agent || typeof agent !== "object") return false;
  const id = String(agent.id || agent.agent_id || "").trim().toLowerCase();
  if (id.startsWith("reflection:")) return true;
  const provenance =
    agent.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
  const kind = String(provenance?.kind || agent.kind || "").trim().toLowerCase();
  return kind === "background_reflection";
};

const backgroundWorkIdentity = (agent) => {
  if (!isBackgroundWorkAgent(agent)) return "";
  const events = Array.isArray(agent?.events) ? agent.events : [];
  const taskIdCandidates = [
    agent?.task_id,
    agent?.metadata?.task_id,
    agent?.metadata?.reflection?.task_id,
    agent?.provenance?.task_id,
    ...events
      .slice()
      .reverse()
      .flatMap((entry) => [
        entry?.task_id,
        entry?.metadata?.task_id,
        entry?.metadata?.reflection?.task_id,
      ]),
  ];
  const taskId = taskIdCandidates
    .map((value) => String(value || "").trim())
    .find(Boolean);
  if (taskId) return `task:${taskId.toLowerCase()}`;

  const id = String(agent?.id || agent?.agent_id || agent?.session_id || "")
    .trim()
    .toLowerCase();
  const normalizedId = id.replace(/^(?:task|background|reflection):/, "");
  return normalizedId ? `task:${normalizedId}` : "";
};

const backgroundAgentTimestamp = (agent) => {
  const direct = Number(agent?.updatedAt ?? agent?.updated_at ?? agent?.timestamp);
  if (Number.isFinite(direct)) return direct;
  const events = Array.isArray(agent?.events) ? agent.events : [];
  return events.reduce((latest, entry) => {
    const timestamp = Number(entry?.timestamp);
    return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest;
  }, 0);
};

const backgroundEventIdentity = (entry) =>
  [
    entry?.id,
    entry?.request_id,
    entry?.task_id,
    entry?.type,
    entry?.name,
    entry?.status,
    entry?.timestamp,
    normalizePreviewText(
      entry?.content || entry?.text || entry?.message || entry?.description,
    ),
  ]
    .map((value) => String(value ?? "").trim())
    .join("|");

const mergeBackgroundAgentRecords = (left, right) => {
  const newer =
    backgroundAgentTimestamp(right) >= backgroundAgentTimestamp(left) ? right : left;
  const older = newer === right ? left : right;
  const seenEvents = new Set();
  const events = [
    ...(Array.isArray(older?.events) ? older.events : []),
    ...(Array.isArray(newer?.events) ? newer.events : []),
  ]
    .filter((entry) => {
      const key = backgroundEventIdentity(entry);
      if (seenEvents.has(key)) return false;
      seenEvents.add(key);
      return true;
    })
    .sort((a, b) => (Number(a?.timestamp) || 0) - (Number(b?.timestamp) || 0));
  return {
    ...older,
    ...newer,
    events,
  };
};

const dedupeBackgroundWorkAgents = (agents) => {
  const output = [];
  const identityIndexes = new Map();
  (Array.isArray(agents) ? agents : []).forEach((agent) => {
    const identity = backgroundWorkIdentity(agent);
    if (!identity) {
      output.push(agent);
      return;
    }
    const existingIndex = identityIndexes.get(identity);
    if (existingIndex === undefined) {
      identityIndexes.set(identity, output.length);
      output.push(agent);
      return;
    }
    output[existingIndex] = mergeBackgroundAgentRecords(output[existingIndex], agent);
  });
  return output;
};

const GENERIC_REFLECTION_LABELS = new Set([
  "reflection worker",
  "background reflection",
  "reflection",
]);

const isGenericReflectionLabel = (value) =>
  GENERIC_REFLECTION_LABELS.has(String(value || "").trim().toLowerCase());

const latestReflectionMetadata = (agent, events = []) => {
  const candidates = [
    ...(Array.isArray(events) ? events : []),
    ...(Array.isArray(agent?.events) ? agent.events : []),
    agent,
  ];
  const merged = {};
  candidates.forEach((candidate) => {
    const metadata =
      candidate?.metadata && typeof candidate.metadata === "object"
        ? candidate.metadata
        : null;
    if (metadata?.reflection) {
      Object.assign(merged, metadata);
    }
  });
  return Object.keys(merged).length ? merged : {};
};

const reflectionDepthBudget = (metadata = {}) => {
  const explicit = Number(metadata.depth_budget);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const patience = Math.max(0, Math.min(3, Number(metadata.patience) || 1));
  return { 0: 1, 1: 2, 2: 3, 3: 4 }[patience] || 2;
};

const formatReflectionPassBudget = (metadata = {}) => {
  const budget = reflectionDepthBudget(metadata);
  const runCount = Math.max(0, Number(metadata.run_count) || 0);
  return `${Math.min(runCount, budget)}/${budget} passes`;
};

const formatReflectionSourceLabel = (metadata = {}) => {
  const sourceMode = String(metadata.source_mode || "").trim().toLowerCase();
  const sourceThread = String(metadata.source_thread_id || "").trim();
  const memoryKeys = Array.isArray(metadata.memory_keys)
    ? metadata.memory_keys.filter((key) => String(key || "").trim())
    : [];
  if (memoryKeys.length) {
    return memoryKeys.length === 1
      ? `memory ${String(memoryKeys[0]).trim()}`
      : `${memoryKeys.length} memories`;
  }
  if (sourceMode === "current" && sourceThread) return "this chat";
  if (sourceMode === "recent") return "recent chat";
  if (sourceMode === "manual" || sourceMode === "user") return "manual topic";
  if (sourceMode === "reflection") return "seeded candidate";
  if (sourceThread) return `chat ${shortOpaqueId(sourceThread, 8)}`;
  return sourceMode || "manual topic";
};

const formatReflectionSurfaceLabel = (metadata = {}) => {
  if (!metadata.run_id) return "queued";
  if (metadata.should_surface_to_user === true) return "surfaced";
  if (metadata.should_surface_to_user === false) return "quiet note";
  return "saved";
};

const formatReflectionScoreLabel = (metadata = {}) => {
  const usefulness = Number(metadata.usefulness);
  const novelty = Number(metadata.novelty);
  if (!Number.isFinite(usefulness) && !Number.isFinite(novelty)) return "";
  const parts = [];
  if (Number.isFinite(usefulness)) parts.push(`use ${usefulness.toFixed(2)}`);
  if (Number.isFinite(novelty)) parts.push(`new ${novelty.toFixed(2)}`);
  return parts.join(" / ");
};

const cleanReflectionPreview = (value) => {
  let text = normalizePreviewText(value);
  if (!text) return "";
  text = text.replace(/^Reflection saved:\s*/i, "").trim();
  text = text.replace(/^Reflection:\s*/i, "").trim();
  const claimMatch = text.match(/^Claim:\s*(.*?)(?:\s+Why it matters:|$)/i);
  if (claimMatch?.[1]) {
    return `Claim: ${claimMatch[1].trim()}`;
  }
  return text;
};

const reflectionEventLabel = (entry) => {
  const status = normalizeStatusValue(entry?.status);
  if (status && status !== "unknown") {
    if (status === "resolved") return "saved";
    if (status === "active") return "running";
    return status;
  }
  if (entry?.type === "thought") return "note";
  return entry?.type || "update";
};

const buildReflectionInspectorRows = (agent, metadata = {}) => {
  const status = String(agent?.status || metadata.task_status || "").trim() || "unknown";
  const rows = [
    { label: "Source", value: formatReflectionSourceLabel(metadata) },
    { label: "Status", value: status },
    { label: "Pass budget", value: formatReflectionPassBudget(metadata) },
    { label: "Patience", value: "reflection passes only" },
    { label: "Surfacing", value: formatReflectionSurfaceLabel(metadata) },
  ];
  const score = formatReflectionScoreLabel(metadata);
  if (score) rows.push({ label: "Scores", value: score });
  const contextCount = Number(metadata.input_context_count);
  if (Number.isFinite(contextCount)) {
    rows.push({ label: "Context inputs", value: String(contextCount) });
  }
  rows.push({
    label: "Next",
    value:
      status === "active" || status === "running"
        ? "wait or stop from the card controls"
        : "open the saved reflection when it surfaces",
  });
  return rows;
};

const THREAD_COLOR_PALETTE = ["#9B8CFF", "#B29ED9", "#6B7AD6", "#86EAA0", "#21B228"];

const getThreadColor = (conversationId) => {
  const normalized = String(conversationId || "").trim();
  if (!normalized) return THREAD_COLOR_PALETTE[0];
  const hash = normalized
    .split("")
    .reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return THREAD_COLOR_PALETTE[hash % THREAD_COLOR_PALETTE.length];
};

const activityEntryKey = (agent, entry) =>
  `${agent?.id || agent?.agent_id || agent?.session_id || "agent"}-${
    entry?.id || entry?.request_id || entry?.timestamp || "event"
  }-${entry?.type || "log"}-${entry?.name || "entry"}`;

const resolveToolChatKey = (entry, agent, fallbackSessionId = "") => {
  const provenance =
    agent?.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
  return String(
    entry?.session_id ||
      agent?.session_id ||
      agent?.conversationId ||
      agent?.conversation_id ||
      provenance?.parent_session_id ||
      provenance?.branch_session_id ||
      fallbackSessionId ||
      "",
  ).trim();
};

const isToolOnlyAgent = (agent) => {
  const events = getRenderableAgentActivity(agent, { showToolEntries: true });
  return events.length > 0 && events.every((entry) => entry?.type === "tool");
};

const resolveAgentToolChatKey = (agent, fallbackSessionId = "") => {
  if (!isToolOnlyAgent(agent)) return "";
  const events = getRenderableAgentActivity(agent, { showToolEntries: true });
  return resolveToolChatKey(events[0], agent, fallbackSessionId);
};

const resolveAgentDisplayLabel = (agent) => {
  if (!agent || typeof agent !== "object") return "orchestrator";
  const id = String(
    agent.id || agent.agent_id || agent.sessionId || agent.session_id || agent.chain_id || "",
  ).trim();
  const explicit = String(
    agent.label || agent.agent_label || agent.name || agent.title || "",
  ).trim();
  const provenance =
    agent.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
  const provenanceLabel = String(provenance?.label || "").trim();
  const kind = String(provenance?.kind || "").trim().toLowerCase();

  if (explicit && !looksLikeUuid(explicit)) {
    if (kind === "background_reflection" && isGenericReflectionLabel(explicit) && provenanceLabel) {
      return provenanceLabel;
    }
    return explicit;
  }
  if (provenanceLabel) {
    return provenanceLabel;
  }
  if (kind === "subchat" || kind === "fork") {
    const shortId = shortOpaqueId(
      provenance?.branch_session_id || agent.conversationId || agent.sessionId || id,
    );
    return shortId ? `${kind} ${shortId}` : kind || "subchat";
  }
  if (kind === "task_chain") {
    const shortId = shortOpaqueId(provenance?.task_id || id);
    return shortId ? `task ${shortId}` : "task chain";
  }
  if (explicit && !looksLikeUuid(explicit)) {
    return explicit;
  }
  if (looksLikeUuid(id)) {
    return `agent ${shortOpaqueId(id)}`;
  }
  return id || "orchestrator";
};

const resolveAgentConversationTarget = (agent, displayLabel = "") => {
  if (!agent || typeof agent !== "object") return null;
  const provenance =
    agent.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
  const kind = String(provenance?.kind || "").trim().toLowerCase();
  const branchSessionId = String(provenance?.branch_session_id || "").trim();
  const directConversationId = String(
    agent.conversationId ||
      agent.conversation_id ||
      agent.sessionId ||
      agent.session_id ||
      "",
  ).trim();
  const id = String(
    agent.id || agent.agent_id || agent.sessionId || agent.session_id || agent.chain_id || "",
  ).trim();
  let conversationId = branchSessionId || directConversationId;
  if (!conversationId && (kind === "subchat" || kind === "fork" || looksLikeUuid(id))) {
    conversationId = id;
  }
  if (!conversationId) return null;
  return {
    conversationId,
    label: String(displayLabel || agent.label || "").trim() || conversationId,
    kind: kind || "conversation",
    buttonLabel: kind === "subchat" || kind === "fork" ? "open subchat" : "open chat",
  };
};

const renderConsolePip = ({ key, label, value, title, tone = "", compact = false }) => {
  const pipTitle = [label, value]
    .filter((part) => typeof part === "string" && part.trim())
    .join(": ");
  const fullTitle = [pipTitle, title].filter(Boolean).join(" - ");
  const visibleLabel = compact ? compactConsolePipLabel(label) : label;
  const visibleValue = compact ? compactConsolePipValue(value, label) : value;

  return (
    <span
      key={key}
      className={`agent-console-pip${compact ? " agent-console-pip--compact" : ""}`}
      data-tone={tone || undefined}
      title={fullTitle}
    >
      {compact ? <span className="agent-console-pip-light" aria-hidden="true" /> : null}
      <span className="agent-console-pip-copy">
        <span className="agent-console-pip-label">{visibleLabel}</span>
        <span className="agent-console-pip-value">{visibleValue}</span>
      </span>
    </span>
  );
};

const formatHandoffMeta = (handoff) => {
  if (!handoff || typeof handoff !== "object") return "";
  const summary =
    typeof handoff.summary === "string" ? handoff.summary.trim() : "";
  const goalCount = Array.isArray(handoff.open_goals) ? handoff.open_goals.length : 0;
  const approvalCount = Array.isArray(handoff.pending_approvals)
    ? handoff.pending_approvals.length
    : 0;
  const counts = [];
  if (goalCount) counts.push(`${goalCount} goal${goalCount === 1 ? "" : "s"}`);
  if (approvalCount) {
    counts.push(`${approvalCount} approval${approvalCount === 1 ? "" : "s"}`);
  }
  if (!summary && !counts.length) return "";
  return counts.length ? `${summary || "handoff"} (${counts.join(", ")})` : summary;
};

const controlModeTitle = (mode) => {
  const key = typeof mode === "string" ? mode.trim().toLowerCase() : "";
  if (key === "runtime_revoke") {
    return "Requests cancellation. External or non-cooperative work can keep running until it acknowledges the request.";
  }
  if (key === "queued_request") return "Stores a redirect request for the delegated run.";
  if (key === "soft") return "Updates delegated-run state in the console for now.";
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

const isToolAwaitingReview = (tool) => {
  const status = getEffectiveToolStatus(tool);
  return !status || status === "proposed" || status === "pending";
};

const buildToolStateInspectorRows = (entry, context = {}) => {
  if (!entry || entry.type !== "tool") return [];
  const status = context.status || getEffectiveToolStatus(entry) || normalizeToolStatus(entry.status);
  const requestId = String(entry.id || entry.request_id || "").trim();
  const chainId = String(context.chainIdentifier || entry.chain_id || entry.message_id || "").trim();
  const sessionId = String(entry.session_id || context.sessionId || "").trim();
  const owner = String(context.agentId || entry.agent_id || entry.agent || "").trim();
  const isPending = status === "proposed" || status === "pending" || !status;
  return [
    { label: "Source", value: context.sourceLabel || "tool event" },
    { label: "Status", value: status || "pending" },
    { label: "Owner", value: owner || sessionId || "current console" },
    { label: "Request", value: requestId || chainId || "local fallback" },
    {
      label: "Evidence",
      value: entry.synthetic
        ? "saved conversation tool state"
        : requestId
          ? "backend Agent Console event"
          : "live console activity",
    },
    {
      label: "Next",
      value: isPending
        ? "Approve, edit, or deny this tool."
        : "Open the activity to inspect arguments and result.",
    },
  ];
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
      return JSON.stringify({ name: t?.name, args: t?.args || {} }) === sig;
    });
  }
  if (idx >= 0) {
    list[idx] = { ...list[idx], ...update };
  } else {
    list.push(update);
  }
  return list;
};

const mergeToolUpdates = (tools, updates) =>
  (Array.isArray(updates) ? updates : []).reduce(
    (current, update) => mergeToolUpdate(current, update),
    Array.isArray(tools) ? tools : [],
  );


const summarizeToolBatchLabel = (tools) => {
  const names = (Array.isArray(tools) ? tools : [])
    .map((tool) => resolveToolDisplayName(tool, ""))
    .filter(Boolean);
  if (!names.length) return "tool activity";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} + ${names[1]}`;
  return `${names[0]} +${names.length - 1}`;
};


const summarizeSyntheticAgentStatus = (events) => {
  const statuses = (Array.isArray(events) ? events : [])
    .map((entry) => getEffectiveToolStatus(entry) || normalizeToolStatus(entry?.status))
    .filter(Boolean);
  if (statuses.some((status) => status === "proposed" || status === "pending")) {
    return "pending";
  }
  if (statuses.some((status) => status === "error" || status === "timeout")) {
    return "error";
  }
  return "active";
};


const buildSyntheticToolEventTimestamp = (tool, message, fallbackSeconds, index = 0) => {
  if (typeof tool?.timestamp === "number" && Number.isFinite(tool.timestamp)) {
    return tool.timestamp + index * 0.001;
  }
  const resolved = resolveEventTimestamp(
    tool?.updated_at || tool?.created_at || message?.timestamp || message?.updated_at,
  );
  if (resolved) {
    return resolved / 1000 + index * 0.001;
  }
  return fallbackSeconds + index * 0.001;
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
  onOpenConversation,
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
  const [collapsedToolChats, setCollapsedToolChats] = React.useState({});
  const [collapsedAgents, setCollapsedAgents] = React.useState({});
  const [expandedAgents, setExpandedAgents] = React.useState({});
  const [hiddenAgents, setHiddenAgents] = React.useState({});
  const [actionHistoryCollapsed, setActionHistoryCollapsed] = React.useState(true);
  const [actionHistoryHidden, setActionHistoryHidden] = React.useState(false);
  const [runtimeStatus, setRuntimeStatus] = React.useState(null);
  const [runtimeLoading, setRuntimeLoading] = React.useState(false);
  const [runtimeError, setRuntimeError] = React.useState("");
  const [serverRuntime, setServerRuntime] = React.useState(null);
  const [serverRuntimeLoading, setServerRuntimeLoading] = React.useState(false);
  const [serverRuntimeError, setServerRuntimeError] = React.useState("");
  const [runtimePanelCollapsed, setRuntimePanelCollapsed] = React.useState(true);
  const [runtimePanelHidden, setRuntimePanelHidden] = React.useState(false);
  const [backgroundPanelCollapsed, setBackgroundPanelCollapsed] = React.useState(true);
  const [backgroundPanelHidden, setBackgroundPanelHidden] = React.useState(false);
  const [backgroundPromptOpen, setBackgroundPromptOpen] = React.useState(false);
  const [backgroundComposerMode, setBackgroundComposerMode] = React.useState("prompt");
  const [backgroundPromptDraft, setBackgroundPromptDraft] = React.useState("");
  const [backgroundPromptFeedback, setBackgroundPromptFeedback] = React.useState("");
  const [backgroundPromptPending, setBackgroundPromptPending] = React.useState(false);
  const [reflectionQuestionDraft, setReflectionQuestionDraft] = React.useState("");
  const [reflectionSourceMode, setReflectionSourceMode] = React.useState("current");
  const [reflectionMemoryKey, setReflectionMemoryKey] = React.useState("");
  const [reflectionPatience, setReflectionPatience] = React.useState("1");
  const [reflectionFeedback, setReflectionFeedback] = React.useState("");
  const [reflectionPending, setReflectionPending] = React.useState(false);
  const [providerStatus, setProviderStatus] = React.useState(null);
  const [providerModels, setProviderModels] = React.useState([]);
  const [providerLogs, setProviderLogs] = React.useState([]);
  const [, setProviderLogsCursor] = React.useState(0);
  const [providerLogsOpen, setProviderLogsOpen] = React.useState(false);
  const [providerSelectedModel, setProviderSelectedModel] = React.useState("");
  const [providerContextDraft, setProviderContextDraft] = React.useState("");
  const [providerPendingAction, setProviderPendingAction] = React.useState("");
  const [providerActionError, setProviderActionError] = React.useState("");
  const [runtimeNow, setRuntimeNow] = React.useState(() => Date.now());
  const [runtimeRagOperation, setRuntimeRagOperation] = React.useState(null);
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
  const [, setLoadError] = React.useState("");
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
  const [syncInboxCollapsed, setSyncInboxCollapsed] = React.useState(true);
  const [syncInboxHidden, setSyncInboxHidden] = React.useState(false);
  const [browserSessionPopup, setBrowserSessionPopup] = React.useState(null);
  const [browserPopupPendingAction, setBrowserPopupPendingAction] = React.useState("");
  const [browserPopupError, setBrowserPopupError] = React.useState("");
  const [browserNavigateDraft, setBrowserNavigateDraft] = React.useState("");
  const [browserTypeDraft, setBrowserTypeDraft] = React.useState("");
  const [browserKeyDraft, setBrowserKeyDraft] = React.useState("Enter");
  const [agentControlPendingKey, setAgentControlPendingKey] = React.useState("");
  const [agentControlFeedback, setAgentControlFeedback] = React.useState("");
  const [toolBatchPendingKey, setToolBatchPendingKey] = React.useState("");
  const [redirectEditorAgentId, setRedirectEditorAgentId] = React.useState("");
  const [redirectNoteDraft, setRedirectNoteDraft] = React.useState("");
  const [redirectWorkflowDraft, setRedirectWorkflowDraft] = React.useState("");
  const providerActionPending = Boolean(providerPendingAction);
  const sidebarRef = React.useRef(null);
  const focusTokenRef = React.useRef(null);
  const runtimeRagClearTimerRef = React.useRef(null);
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
  const providerAutoSelectedModelRef = React.useRef("");
  const providerLogsCursorRef = React.useRef(0);
  const providerLogsOpenRef = React.useRef(false);
  const providerActionPendingRef = React.useRef(false);
  const lastVerifyRef = React.useRef({ model: null, at: 0 });
  const syncInboxInteractedRef = React.useRef(false);
  const selectedDirectLocalModel = React.useMemo(
    () => resolveRuntimeModelLabel({ state, runtime: runtimeStatus }),
    [runtimeStatus, state.localModel, state.transformerModel],
  );
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
  const backgroundQueueAgent = React.useMemo(
    () =>
      (Array.isArray(agents) ? agents : []).find(
        (agent) => String(agent?.id || "").trim() === "system:celery",
      ) || null,
    [agents],
  );
  const backgroundAutonomyAgent = React.useMemo(
    () =>
      (Array.isArray(agents) ? agents : []).find(
        (agent) => String(agent?.id || "").trim() === "system:background-autonomy",
      ) || null,
    [agents],
  );
  const backgroundRunAgents = React.useMemo(
    () =>
      (Array.isArray(agents) ? agents : []).filter((agent) => {
        const id = String(agent?.id || "").trim();
        return !id.startsWith("system:") && isBackgroundWorkAgent(agent) && !isReflectionAgent(agent);
      }),
    [agents],
  );
  const queueJobCount = React.useMemo(() => {
    const resources = backgroundQueueAgent?.resources;
    if (!resources || typeof resources !== "object") return 0;
    return ["active_count", "reserved_count", "scheduled_count"].reduce(
      (total, key) => total + Math.max(0, Number(resources[key]) || 0),
      0,
    );
  }, [backgroundQueueAgent]);
  const activeAgentCount = React.useMemo(
    () => {
      const promptTaskRuns = dedupeBackgroundWorkAgents(backgroundRunAgents).filter(
        (agent) => isActiveRunStatus(agent?.status),
      ).length;
      return promptTaskRuns + (isActiveRunStatus(backgroundAutonomyAgent?.status) ? 1 : 0);
    },
    [backgroundAutonomyAgent, backgroundRunAgents],
  );
  const backgroundRunCount = React.useMemo(() => {
    const runKeys = new Set();
    backgroundRunAgents.forEach((agent, index) => {
      const agentRunIds = new Set();
      const addRunId = (value) => {
        const runId = String(value || "").trim();
        if (runId) agentRunIds.add(runId);
      };
      addRunId(agent?.run_id);
      addRunId(agent?.metadata?.run_id);
      (Array.isArray(agent?.events) ? agent.events : []).forEach((event) => {
        addRunId(event?.run_id);
        addRunId(event?.metadata?.run_id);
      });
      if (agentRunIds.size) {
        agentRunIds.forEach((runId) => runKeys.add(`run:${runId}`));
        return;
      }
      const agentId = String(agent?.id || agent?.agent_id || agent?.session_id || "").trim();
      runKeys.add(`agent:${agentId || index}`);
    });
    return runKeys.size;
  }, [backgroundRunAgents]);
  const persistCalendarTask = React.useCallback(
    async (payload) => {
      const eventId = String(payload?.id || "").trim();
      if (!eventId) {
        throw new Error("Task id is required.");
      }
      await axios.post(`/api/calendar/events/${encodeURIComponent(eventId)}`, payload);
      onRefreshCalendar?.();
      return eventId;
    },
    [onRefreshCalendar],
  );
  const openTaskEditor = React.useCallback(
    ({ task = null, taskPrefill = null } = {}) => {
      setToolEditorState({
        mode: "task",
        task,
        taskPrefill,
        onSaveTask: persistCalendarTask,
      });
    },
    [persistCalendarTask],
  );
  const buildBackgroundTaskPayload = React.useCallback(
    (promptText, startDate = new Date()) => {
      const normalizedPrompt = normalizePreviewText(promptText);
      const safeStart =
        startDate instanceof Date && !Number.isNaN(startDate.getTime())
          ? startDate
          : new Date();
      const startTime = Math.floor(safeStart.getTime() / 1000);
      const promptSlug =
        normalizedPrompt
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/(^-|-$)+/g, "")
          .slice(0, 32) || "prompt";
      return {
        id: `background-${promptSlug}-${startTime}`,
        title: `Background: ${truncatePreviewText(normalizedPrompt, 56)}`,
        description: normalizedPrompt,
        session_id: state.sessionId ? String(state.sessionId) : undefined,
        start_time: startTime,
        end_time: startTime + 15 * 60,
        timezone: preferredTimezone,
        status: "pending",
        actions: [
          {
            kind: "prompt",
            prompt: normalizedPrompt,
            conversation_mode: "new_chat",
            origin_session_id: state.sessionId ? String(state.sessionId) : undefined,
          },
        ],
        background_job: {
          schema_version: 1,
          patience: {
            stop_condition: "one_pass",
            max_attempts: 1,
            max_runtime_seconds: 900,
            satisfied_threshold: 0.8,
          },
          execution: {
            reasoning_effort: "inherit",
            allow_subagents: true,
            sandbox_processes: true,
            permissions: [],
          },
          ownership: {
            owner_kind: state.sessionId ? "conversation" : "calendar_event",
            conversation_id: state.sessionId ? String(state.sessionId) : undefined,
          },
        },
      };
    },
    [preferredTimezone, state.sessionId],
  );
  const scheduleBackgroundPrompt = React.useCallback(() => {
    if (backgroundPromptPending) return;
    const normalizedPrompt = normalizePreviewText(backgroundPromptDraft);
    if (!normalizedPrompt) {
      setBackgroundPromptFeedback("Write a prompt before scheduling it.");
      return;
    }
    openTaskEditor({
      taskPrefill: buildBackgroundTaskPayload(normalizedPrompt),
    });
    setBackgroundPromptFeedback("Opened the task scheduler for this background prompt.");
    setBackgroundPromptDraft("");
    setBackgroundPromptOpen(false);
  }, [
    backgroundPromptDraft,
    backgroundPromptPending,
    buildBackgroundTaskPayload,
    openTaskEditor,
  ]);
  const startBackgroundPrompt = React.useCallback(async () => {
    if (!backendReady || backgroundPromptPending) return;
    const normalizedPrompt = normalizePreviewText(backgroundPromptDraft);
    if (!normalizedPrompt) {
      setBackgroundPromptFeedback("Write a prompt before running it.");
      return;
    }
    const payload = buildBackgroundTaskPayload(normalizedPrompt, new Date(Date.now() - 1000));
    setBackgroundPromptPending(true);
    setBackgroundPromptFeedback("Running prompt in a new chat...");
    try {
      const eventId = await persistCalendarTask(payload);
      await axios.post(`/api/calendar/events/${encodeURIComponent(eventId)}/run?force=true`, null);
      onRefreshAgents?.();
      onRefreshCalendar?.();
      setBackgroundPromptFeedback("Prompt started in a new chat.");
      setBackgroundPromptDraft("");
      setBackgroundPromptOpen(false);
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Unable to start background prompt.";
      setBackgroundPromptFeedback(String(detail));
    } finally {
      setBackgroundPromptPending(false);
    }
  }, [
    backendReady,
    backgroundPromptDraft,
    backgroundPromptPending,
    buildBackgroundTaskPayload,
    onRefreshAgents,
    onRefreshCalendar,
    persistCalendarTask,
  ]);
  const submitReflectionTask = React.useCallback(
    async (runNow) => {
      if (!backendReady || reflectionPending) return;
      const normalizedQuestion = normalizePreviewText(reflectionQuestionDraft);
      if (!normalizedQuestion) {
        setReflectionFeedback("Write a question before saving the reflection.");
        return;
      }
      const sourceMode = String(reflectionSourceMode || "manual").toLowerCase();
      const memoryKey = reflectionMemoryKey.trim();
      if (sourceMode === "memory" && !memoryKey) {
        setReflectionFeedback("Add a memory key or choose a different source.");
        return;
      }
      const patienceValue = Math.max(0, Math.min(3, Number(reflectionPatience) || 0));
      const payload = {
        title: `Reflection: ${truncatePreviewText(normalizedQuestion, 64)}`,
        question: normalizedQuestion,
        source: "user",
        source_thread_id:
          sourceMode === "current" && state.sessionId ? String(state.sessionId) : "",
        patience: patienceValue,
        memory_keys: sourceMode === "memory" ? [memoryKey] : [],
        metadata: { source_mode: sourceMode },
        run_now: Boolean(runNow),
      };
      setReflectionPending(true);
      setReflectionFeedback(
        runNow ? "Running reflection..." : "Saving and scoring reflection...",
      );
      try {
        await axios.post("/api/reflections/tasks", payload);
        onRefreshAgents?.();
        setReflectionFeedback(
          runNow ? "Reflection started." : "Reflection saved and scored.",
        );
        setReflectionQuestionDraft("");
        setReflectionMemoryKey("");
        setBackgroundPromptOpen(false);
      } catch (err) {
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "Unable to save reflection.";
        setReflectionFeedback(String(detail));
      } finally {
        setReflectionPending(false);
      }
    },
    [
      backendReady,
      onRefreshAgents,
      reflectionMemoryKey,
      reflectionPatience,
      reflectionPending,
      reflectionQuestionDraft,
      reflectionSourceMode,
      state.sessionId,
    ],
  );
  const applyProviderSnapshot = React.useCallback((payload) => {
    const runtime = payload?.runtime || null;
    const models = filterChatCapableProviderModels(payload?.models);
    setProviderStatus(runtime);
    setProviderModels(models);
    const effectiveModel =
      typeof runtime?.effective_model_id === "string"
        ? runtime.effective_model_id.trim()
        : "";
    const loadedModel =
      typeof runtime?.loaded_model === "string" ? runtime.loaded_model.trim() : "";
    const preferredSnapshotModel =
      effectiveModel ||
      models[0] ||
      (isChatCapableProviderModelName(loadedModel) ? loadedModel : "");
    const previousAutoSelectedModel = providerAutoSelectedModelRef.current;
    providerAutoSelectedModelRef.current = preferredSnapshotModel;
    setProviderSelectedModel((prev) => {
      const current = typeof prev === "string" ? prev.trim() : "";
      if (!current) return preferredSnapshotModel;
      if (current === previousAutoSelectedModel) return preferredSnapshotModel;
      return prev;
    });
    if (runtime?.context_length) {
      setProviderContextDraft(String(runtime.context_length));
    }
  }, []);
  const backgroundReflectionAgents = React.useMemo(
    () => dedupeBackgroundWorkAgents(agents).filter(isReflectionAgent),
    [agents],
  );

  const renderBackgroundPanel = React.useCallback(() => {
    if (backgroundPanelHidden) return null;
    const taskLabel = queueJobCount === 1 ? "1 worker job" : `${queueJobCount} worker jobs`;
    const activeLabel =
      activeAgentCount === 1 ? "1 active run" : `${activeAgentCount} active runs`;
    const reflectionCount = backgroundReflectionAgents.length;
    const reflectionLabel =
      reflectionCount === 1 ? "1 reflection" : `${reflectionCount} reflections`;
    const backgroundSubtitle = `${activeLabel}, ${reflectionLabel}, ${taskLabel}`;
    const backgroundDetail = [backgroundAutonomyAgent?.summary, backgroundQueueAgent?.summary]
      .map((value) => (typeof value === "string" ? value.trim() : ""))
      .filter(Boolean)
      .join(" · ") || backgroundSubtitle;
    const autonomyResources =
      backgroundAutonomyAgent?.resources &&
      typeof backgroundAutonomyAgent.resources === "object"
        ? backgroundAutonomyAgent.resources
        : {};
    const autonomyMode = String(autonomyResources.mode || "manual").replaceAll("_", " ");
    const autonomyStatus = String(backgroundAutonomyAgent?.status || "idle");
    const routineValue = autonomyResources.enabled
      ? `${autonomyMode} · ${autonomyStatus}`
      : "disabled";
    const runCount = backgroundRunCount;
    const backgroundInspector = (
      <StateInspector
        title="Background work"
        summary="Agent Console owns manual and scheduled background runs. Settings owns the opt-in routine scheduler and its safety budgets."
        rows={[
          { label: "Routine autonomy", value: routineValue },
          {
            label: "Worker queue",
            value: backgroundQueueAgent?.summary || "No worker snapshot",
          },
          {
            label: "Recorded runs",
            value: `${runCount} prompt/task run${runCount === 1 ? "" : "s"}, ${reflectionCount} reflection${reflectionCount === 1 ? "" : "s"}`,
          },
          { label: "Run now", value: "start here in Agent Console" },
          { label: "Schedule once", value: "save here as a calendar task" },
          { label: "Routine review", value: "configure in Settings > Background" },
        ]}
        label="?"
        ariaLabel="Explain background work"
      />
    );
    const openBackgroundPromptComposer = () => {
      setBackgroundPromptFeedback("");
      setReflectionFeedback("");
      setBackgroundComposerMode("prompt");
      setBackgroundPromptOpen(true);
    };
    const closeComposer = () => {
      setBackgroundPromptOpen(false);
      setBackgroundPromptDraft("");
      setBackgroundPromptFeedback("");
      setReflectionQuestionDraft("");
      setReflectionMemoryKey("");
      setReflectionFeedback("");
      setBackgroundComposerMode("prompt");
    };
    const reflectionCards = backgroundReflectionAgents
      .filter((agent) => {
        const key = String(agent?.id || agent?.agent_id || agent?.session_id || "").trim();
        return !key || !hiddenAgents[key];
      })
      .map((agent) => {
        const reflectionKey = String(
          agent?.id || agent?.agent_id || agent?.session_id || agent?.label || "",
        );
        const reflectionTone = statusTone(agent.status);
        const label = resolveAgentDisplayLabel(agent);
        const allEvents = getRenderableAgentActivity(agent, { showToolEntries: false });
        const events = allEvents.slice(-4).reverse();
        const reflectionMeta = latestReflectionMetadata(agent, allEvents);
        const previewSource =
          [...events].find((entry) =>
            normalizePreviewText(
              entry?.content || entry?.text || entry?.message || entry?.description,
            ),
          ) || {};
        const preview = truncatePreviewText(
          normalizePreviewText(
            previewSource.content ||
              previewSource.text ||
              previewSource.message ||
              previewSource.description ||
              agent.summary ||
              "",
          ),
          140,
        );
        const reflectionPreview = cleanReflectionPreview(preview);
        const conversationTarget = resolveAgentConversationTarget(agent, label);
        const canOpenConversation =
          conversationTarget && typeof onOpenConversation === "function";
        const sourceLabel = formatReflectionSourceLabel(reflectionMeta);
        const budgetLabel = formatReflectionPassBudget(reflectionMeta);
        const surfaceLabel = formatReflectionSurfaceLabel(reflectionMeta);
        const scoreLabel = formatReflectionScoreLabel(reflectionMeta);
        return (
          <article
            key={reflectionKey || label}
            className="agent-background-reflection-card"
            title={label}
          >
            <div className="agent-background-reflection-header">
              <span
                className="agent-status-dot"
                style={{ backgroundColor: reflectionTone.hue }}
                aria-hidden="true"
              />
              <strong>{label}</strong>
              <span className="agent-status-label">{reflectionTone.label}</span>
              <StateInspector
                title="Reflection state"
                summary="This is a bounded reflection worker. Its patience value controls how many reflection passes this task may take."
                rows={buildReflectionInspectorRows(agent, reflectionMeta)}
                label="?"
                className="agent-background-reflection-inspector"
                ariaLabel={`Explain ${label}`}
              />
              {agent.updatedAt ? <time>{formatTimestamp(agent.updatedAt)}</time> : null}
              {canOpenConversation ? (
                <button
                  type="button"
                  className="agent-card-control-btn agent-open-chat-btn"
                  onClick={() =>
                    onOpenConversation(
                      conversationTarget.conversationId,
                      conversationTarget.label,
                    )
                  }
                  title={`${conversationTarget.buttonLabel}: ${conversationTarget.label}`}
                >
                  {conversationTarget.buttonLabel}
                </button>
              ) : null}
            </div>
            <div className="agent-background-reflection-meta" aria-label="reflection metadata">
              <span title="Source context selected for this reflection">{sourceLabel}</span>
              <span title="Patience controls reflection passes only">{budgetLabel}</span>
              <span title="Whether this run created a surfaced reflection conversation">{surfaceLabel}</span>
              {scoreLabel ? <span title="Evaluator usefulness and novelty">{scoreLabel}</span> : null}
            </div>
            {reflectionPreview ? (
              <p className="agent-background-reflection-preview" title={reflectionPreview}>
                {reflectionPreview}
              </p>
            ) : null}
            {events.length ? (
              <div className="agent-background-reflection-events">
                {events.map((entry) => {
                  const eventText = truncatePreviewText(
                    normalizePreviewText(
                      entry?.content || entry?.text || entry?.message || entry?.description || "",
                    ),
                    92,
                  );
                  const eventLabel = reflectionEventLabel(entry);
                  return (
                    <div
                      className="agent-background-reflection-event"
                      key={activityEntryKey(agent, entry)}
                    >
                      <span className="agent-resource-pill">{eventLabel}</span>
                      {eventText ? <span>{eventText}</span> : null}
                      {entry.timestamp ? <time>{formatTimestamp(entry.timestamp)}</time> : null}
                    </div>
                  );
                })}
              </div>
            ) : null}
          </article>
        );
      });

    return (
      <ConsoleObjectCard
        title="background work"
        subtitle={backgroundSubtitle}
        preview={backgroundDetail}
        ariaLabel="background"
        className="agent-background-panel"
        collapsed={backgroundPanelCollapsed}
        onToggleCollapsed={() => setBackgroundPanelCollapsed((prev) => !prev)}
        onHide={() => setBackgroundPanelHidden(true)}
        status={backgroundInspector}
        expandLabel="Expand background"
        collapseLabel="Collapse background"
        hideLabel="Hide background"
      >
        <div className="agent-background-composer">
          {reflectionCards.length ? (
            <div className="agent-background-reflection-list" aria-label="background reflections">
              {reflectionCards}
            </div>
          ) : null}
          {backgroundPromptFeedback ? (
            <p className="agent-background-composer-note" role="status">
              {backgroundPromptFeedback}
            </p>
          ) : null}
          {reflectionFeedback ? (
            <p className="agent-background-composer-note" role="status">
              {reflectionFeedback}
            </p>
          ) : null}
          {backgroundPromptOpen ? (
            <div className="agent-background-composer-form">
              <label className="agent-background-composer-label" htmlFor="background-agent-mode">
                Mode
                <select
                  id="background-agent-mode"
                  className="agent-background-composer-input"
                  value={backgroundComposerMode}
                  onChange={(event) => {
                    setBackgroundComposerMode(event.target.value);
                    setBackgroundPromptFeedback("");
                    setReflectionFeedback("");
                  }}
                >
                  <option value="prompt">Scheduled prompt</option>
                  <option value="reflect">Reflection pass</option>
                </select>
              </label>
              {backgroundComposerMode === "reflect" ? (
                <>
                  <label className="agent-background-composer-label" htmlFor="reflection-question">
                    Reflection focus
                  </label>
                  <textarea
                    id="reflection-question"
                    className="agent-background-composer-input"
                    value={reflectionQuestionDraft}
                    onChange={(event) => {
                      setReflectionQuestionDraft(event.target.value);
                      if (reflectionFeedback) {
                        setReflectionFeedback("");
                      }
                    }}
                    placeholder="Question, tension, or follow-up to think through"
                    rows={3}
                  />
                  <div className="agent-background-composer-grid">
                    <label className="agent-background-composer-label" htmlFor="reflection-source">
                      Source
                      <select
                        id="reflection-source"
                        className="agent-background-composer-input"
                        value={reflectionSourceMode}
                        onChange={(event) => setReflectionSourceMode(event.target.value)}
                      >
                        <option value="current">This chat</option>
                        <option value="recent">Latest chat</option>
                        <option value="memory">Memory key</option>
                        <option value="manual">No linked source</option>
                      </select>
                    </label>
                    <label className="agent-background-composer-label" htmlFor="reflection-patience">
                      Pass budget
                      <select
                        id="reflection-patience"
                        className="agent-background-composer-input"
                        value={reflectionPatience}
                        onChange={(event) => setReflectionPatience(event.target.value)}
                      >
                        <option value="0">1 reflection pass</option>
                        <option value="1">2 reflection passes</option>
                        <option value="2">3 reflection passes</option>
                        <option value="3">4 reflection passes</option>
                      </select>
                    </label>
                  </div>
                  {reflectionSourceMode === "memory" ? (
                    <label className="agent-background-composer-label" htmlFor="reflection-memory-key">
                      Memory key
                      <input
                        id="reflection-memory-key"
                        className="agent-background-composer-input"
                        value={reflectionMemoryKey}
                        onChange={(event) => setReflectionMemoryKey(event.target.value)}
                      />
                    </label>
                  ) : null}
                  <div className="agent-background-composer-actions">
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={
                        !backendReady || reflectionPending || !reflectionQuestionDraft.trim()
                      }
                      onClick={() => submitReflectionTask(true)}
                      title="Run one bounded reflection pass now"
                    >
                      {reflectionPending ? "Working..." : "Run reflection"}
                    </button>
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={reflectionPending || !reflectionQuestionDraft.trim()}
                      onClick={() => submitReflectionTask(false)}
                      title="Save and score this reflection for a later pass"
                    >
                      Save &amp; score
                    </button>
                    <button
                      type="button"
                      className="agent-card-control-btn danger"
                      disabled={reflectionPending}
                      onClick={closeComposer}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <label className="agent-background-composer-label" htmlFor="background-agent-prompt">
                    Scheduled prompt
                  </label>
                  <textarea
                    id="background-agent-prompt"
                    className="agent-background-composer-input"
                    value={backgroundPromptDraft}
                    onChange={(event) => {
                      setBackgroundPromptDraft(event.target.value);
                      if (backgroundPromptFeedback) {
                        setBackgroundPromptFeedback("");
                      }
                    }}
                    placeholder="Prompt to run as a scheduled background chat or task"
                    rows={3}
                  />
                  <div className="agent-background-composer-actions">
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={
                        !backendReady || backgroundPromptPending || !backgroundPromptDraft.trim()
                      }
                      onClick={startBackgroundPrompt}
                      title="Run prompt in new chat"
                    >
                      {backgroundPromptPending ? "Running..." : "Run in new chat"}
                    </button>
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={backgroundPromptPending || !backgroundPromptDraft.trim()}
                      onClick={scheduleBackgroundPrompt}
                      title="Schedule this background prompt"
                    >
                      Schedule
                    </button>
                    <button
                      type="button"
                      className="agent-card-control-btn danger"
                      disabled={backgroundPromptPending}
                      onClick={closeComposer}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              )}
            </div>
          ) : (
            <div className="agent-background-compose-buttons">
              <button
                type="button"
                className="agent-background-compose-trigger"
                onClick={openBackgroundPromptComposer}
                aria-label="Add background work"
                title="Add background work"
              >
                <span aria-hidden="true" className="agent-background-compose-rule">
                  --
                </span>
                <span aria-hidden="true" className="agent-background-compose-plus">
                  +
                </span>
                <span aria-hidden="true" className="agent-background-compose-rule">
                  --
                </span>
              </button>
            </div>
          )}
        </div>
      </ConsoleObjectCard>
    );
  }, [
    activeAgentCount,
    backgroundAutonomyAgent,
    backgroundComposerMode,
    backgroundPanelCollapsed,
    backgroundPanelHidden,
    backgroundPromptDraft,
    backgroundPromptFeedback,
    backgroundPromptOpen,
    backgroundPromptPending,
    backgroundQueueAgent,
    backgroundReflectionAgents,
    backgroundRunCount,
    backendReady,
    hiddenAgents,
    onOpenConversation,
    reflectionFeedback,
    reflectionMemoryKey,
    reflectionPatience,
    reflectionPending,
    reflectionQuestionDraft,
    reflectionSourceMode,
    queueJobCount,
    scheduleBackgroundPrompt,
    startBackgroundPrompt,
    submitReflectionTask,
  ]);
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
    (width, { persist = true } = {}) => {
      const root = typeof document !== "undefined" ? document.documentElement : null;
      if (!root) return;
      root.style.setProperty("--sidebar-width-right", `${width}px`);
      if (!persist) return;
      try {
        localStorage.setItem("sidebarWidthRight", String(width));
      } catch (err) {
        void err;
      }
    },
    [],
  );

  const resetSidebarWidth = React.useCallback(() => {
    const root = typeof document !== "undefined" ? document.documentElement : null;
    if (!root) return;
    root.style.removeProperty("--sidebar-width-right");
    try {
      localStorage.removeItem("sidebarWidthRight");
    } catch (err) {
      void err;
    }
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
      document.body.classList.add("is-layout-resizing");
      setIsResizing(true);
      let lastWidth = startWidth;
      let frameId = null;

      const flushWidth = (persist = false) => {
        if (frameId !== null && typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(frameId);
          frameId = null;
        }
        applySidebarWidth(lastWidth, { persist });
      };

      const onMove = (moveEvent) => {
        const delta = startX - moveEvent.clientX;
        lastWidth = clampSidebarWidth(startWidth + delta, minWidth, maxWidth);
        if (typeof requestAnimationFrame !== "function") {
          applySidebarWidth(lastWidth, { persist: false });
          return;
        }
        if (frameId !== null) return;
        frameId = requestAnimationFrame(() => {
          frameId = null;
          applySidebarWidth(lastWidth, { persist: false });
        });
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        flushWidth(true);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.body.classList.remove("is-layout-resizing");
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
  const currentConversationLabel = React.useMemo(
    () =>
      normalizePreviewText(
        state.conversationTitle ||
          state.currentConversationTitle ||
          state.currentConversationName ||
          state.sessionTitle ||
          state.sessionName ||
          state.chatTitle ||
          "",
      ),
    [
      state.chatTitle,
      state.conversationTitle,
      state.currentConversationName,
      state.currentConversationTitle,
      state.sessionName,
      state.sessionTitle,
    ],
  );
  const resolveToolChatDisplayName = React.useCallback(
    (chatKey, message = null, agent = null) => {
      const meta = message && typeof message === "object" ? message.metadata : null;
      const provenance =
        agent?.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
      const direct = normalizePreviewText(
        meta?.conversation_title ||
          meta?.conversation_name ||
          meta?.chat_title ||
          message?.conversation_title ||
          message?.conversation_name ||
          provenance?.conversation_title ||
          provenance?.conversation_name ||
          agent?.conversation_title ||
          agent?.conversation_name ||
          "",
      );
      if (direct) return direct;
      const normalizedKey = String(chatKey || "").trim();
      if (normalizedKey && normalizedKey === String(state.sessionId || "").trim()) {
        return currentConversationLabel || "current chat";
      }
      return normalizedKey ? `chat ${shortOpaqueId(normalizedKey, 8)}` : "current chat";
    },
    [currentConversationLabel, state.sessionId],
  );
  const actionHistoryByResponse = React.useMemo(
    () => groupActionsByResponse(actions),
    [actions],
  );
  const agentToolResponseIds = React.useMemo(() => {
    const ids = new Set();
    agents.forEach((agent) => {
      (Array.isArray(agent?.events) ? agent.events : []).forEach((entry) => {
        if (!entry || entry.type !== "tool") return;
        const responseId = String(entry.message_id || entry.chain_id || "").trim();
        if (responseId) ids.add(responseId);
      });
    });
    return ids;
  }, [agents]);
  const syntheticConversationToolAgents = React.useMemo(() => {
    if (!showToolEntries) return [];
    const conversation = Array.isArray(state.conversation) ? state.conversation : [];
    return conversation.flatMap((message) => {
      if (!message || typeof message !== "object") return [];
      const messageId = String(message.id || message.message_id || "").trim();
      if (!messageId || agentToolResponseIds.has(messageId)) return [];
      const tools = Array.isArray(message.tools)
        ? message.tools.filter((tool) => tool && typeof tool === "object")
        : [];
      if (!tools.length) return [];
      const messageTimestampMs = resolveEventTimestamp(
        message.timestamp || message.updated_at || message.created_at,
      );
      const fallbackSeconds = messageTimestampMs
        ? messageTimestampMs / 1000
        : Math.floor(Date.now() / 1000);
      const metadata =
        message.metadata && typeof message.metadata === "object" ? message.metadata : {};
      const events = tools.map((tool, index) => {
        const status =
          getEffectiveToolStatus(tool) || normalizeToolStatus(tool?.status) || "proposed";
        const normalizedArgs =
          tool?.args && typeof tool.args === "object" ? tool.args : {};
        const fallbackResult =
          typeof tool?.result !== "undefined" ? tool.result : fallbackResultForStatus(status);
        return {
          type: "tool",
          id:
            String(tool?.id || tool?.request_id || "").trim()
            || `conversation-tool:${messageId}:${index}`,
          request_id: String(tool?.request_id || tool?.id || "").trim() || undefined,
          name: resolveToolDisplayName(tool),
          args: normalizedArgs,
          ...(typeof fallbackResult !== "undefined" ? { result: fallbackResult } : {}),
          status,
          timestamp: buildSyntheticToolEventTimestamp(tool, message, fallbackSeconds, index),
          chain_id: messageId,
          message_id: messageId,
          session_id: state.sessionId || null,
          mode: metadata.mode,
          model: metadata.model,
        };
      });
      if (!events.length) return [];
      const updatedAt = Math.max(
        ...events.map((entry) => Number(entry?.timestamp) || 0),
        fallbackSeconds,
      );
      const summary =
        normalizePreviewText(message.text || message.content || message.message)
        || `tool call: ${summarizeToolBatchLabel(events)}`;
      return [
        {
          id: `conversation-tools:${messageId}`,
          label: summarizeToolBatchLabel(events),
          status: summarizeSyntheticAgentStatus(events),
          updatedAt,
          summary,
          events,
          session_id: state.sessionId || null,
        },
      ];
    });
  }, [agentToolResponseIds, showToolEntries, state.conversation, state.sessionId]);
  const displayAgents = React.useMemo(
    () => [...(Array.isArray(agents) ? agents : []), ...syntheticConversationToolAgents],
    [agents, syntheticConversationToolAgents],
  );
  const visibleAgents = React.useMemo(
    () =>
      dedupeBackgroundWorkAgents(displayAgents).filter(
        (agent) =>
          !isReflectionAgent(agent) && shouldShowAgentInConsole(agent, { showToolEntries }),
      ),
    [displayAgents, showToolEntries],
  );
  const visibleToolChatStats = React.useMemo(() => {
    const stats = new Map();
    visibleAgents.forEach((agent) => {
      if (!isToolOnlyAgent(agent)) return;
      const chatKey = resolveAgentToolChatKey(agent, state.sessionId);
      if (!chatKey) return;
      const events = getRenderableAgentActivity(agent, { showToolEntries: true }).filter(
        (entry) => entry?.type === "tool",
      );
      if (!events.length) return;
      const chainIdentifier = String(events[0]?.message_id || events[0]?.chain_id || "").trim();
      const message = chainIdentifier ? conversationById.get(chainIdentifier) : null;
      const existing = stats.get(chatKey) || {
        chatKey,
        color: getThreadColor(chatKey),
        label: resolveToolChatDisplayName(chatKey, message, agent),
        agentCount: 0,
        toolCount: 0,
        latestTimestamp: 0,
        preview: "",
      };
      existing.agentCount += 1;
      existing.toolCount += events.length;
      events.forEach((entry) => {
        const ts = Number(entry?.timestamp) || 0;
        if (ts >= existing.latestTimestamp) {
          const bodyText = entry.content || entry.text || entry.message || "...";
          const preview = buildEntryPreview(entry, bodyText);
          existing.latestTimestamp = ts;
          existing.preview = preview?.short || normalizePreviewText(bodyText);
        }
      });
      stats.set(chatKey, existing);
    });
    return stats;
  }, [
    conversationById,
    resolveToolChatDisplayName,
    state.sessionId,
    visibleAgents,
  ]);
  const visibleAgentItems = React.useMemo(() => {
    const collapsedSeen = new Set();
    const openGroups = new Map();
    const items = [];
    visibleAgents.forEach((agent) => {
      const chatKey = resolveAgentToolChatKey(agent, state.sessionId);
      if (!chatKey) {
        items.push({ type: "agent", agent });
        return;
      }
      if (collapsedToolChats[chatKey]) {
        if (collapsedSeen.has(chatKey)) return;
        collapsedSeen.add(chatKey);
        items.push({
          type: "tool-chat-summary",
          key: chatKey,
          summary: visibleToolChatStats.get(chatKey),
        });
        return;
      }
      let group = openGroups.get(chatKey);
      if (!group) {
        group = {
          type: "tool-chat-group",
          key: chatKey,
          summary: visibleToolChatStats.get(chatKey),
          agents: [],
        };
        openGroups.set(chatKey, group);
        items.push(group);
      }
      group.agents.push(agent);
    });
    return items;
  }, [collapsedToolChats, state.sessionId, visibleAgents, visibleToolChatStats]);
  const showStandaloneActionHistory =
    Array.isArray(actions) && actions.length > 0;
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
  const toolResolutionUpdatesRef = React.useRef(new Map());
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
  const formatModelScaleLabel = React.useCallback((modelName) => {
    const raw = String(modelName || "").trim();
    if (!raw) return "";
    const directMatch = raw.match(/(?:^|[-_/])(?:e)?(\d+(?:\.\d+)?)([bm])(?:[-_/]|$)/i);
    const compactMatch = directMatch ? null : raw.match(/(\d+(?:\.\d+)?)([bm])\b/i);
    const match = directMatch || compactMatch;
    if (!match) return "";
    const value = Number(match[1]);
    if (!Number.isFinite(value) || value <= 0) return "";
    const suffix = String(match[2] || "").toUpperCase();
    const whole = Number.isInteger(value) ? String(value) : value.toFixed(1);
    return `${whole}${suffix} params`;
  }, []);
  const formatModelQuantLabel = React.useCallback((modelName) => {
    const raw = String(modelName || "").trim().toLowerCase();
    if (!raw) return "";
    const match = raw.match(/(?:^|[-_/])q(\d+(?:_[a-z0-9]+)?)(?:[-_/]|$)/i);
    if (!match) return "";
    return `Q${String(match[1]).toUpperCase()}`;
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
  const contextBudget = React.useMemo(() => {
    const messages = Array.isArray(state.conversation) ? state.conversation : [];
    const loadedTokens = estimateConversationTokens(messages);
    const effectiveTokens = loadedTokens;
    const tokenLimit = parseContextLength(state.maxContextLength);
    const rawTrimMeta =
      state.conversationTrimMeta &&
      typeof state.conversationTrimMeta === "object" &&
      state.conversationTrimMeta.truncated
        ? state.conversationTrimMeta
        : null;
    const loadedMessages = messages.length;
    const recordedTotalMessages = Number(rawTrimMeta?.total_messages);
    const totalMessages =
      Number.isFinite(recordedTotalMessages) && recordedTotalMessages > loadedMessages
        ? recordedTotalMessages
        : loadedMessages;
    const omittedMessages = Math.max(0, totalMessages - loadedMessages);
    const omittedTools = Number(rawTrimMeta?.omitted_tools);
    const compaction = summarizeConversationCompactions(messages);
    const ratio =
      tokenLimit && effectiveTokens
        ? Math.min(1, Math.max(0, effectiveTokens / tokenLimit))
        : 0;
    const tone =
      tokenLimit && ratio >= 0.9
        ? "error"
        : tokenLimit && ratio >= 0.75
          ? "degraded"
          : tokenLimit
            ? "connected"
            : "idle";
    const totalKnown = !rawTrimMeta || omittedMessages === 0;
    const loadedLabel = formatTokenCount(loadedTokens);
    const currentLabel = totalKnown ? loadedLabel : `loaded ${loadedLabel}`;
    const effectiveLabel = tokenLimit
      ? `${formatTokenCount(effectiveTokens)} / ${formatTokenCount(tokenLimit)}`
      : formatTokenCount(effectiveTokens);
    const compactionLabel =
      compaction.count === 1 ? "1 compaction" : `${compaction.count} compactions`;
    const metaParts = [
      `${loadedMessages}/${totalMessages} messages loaded`,
      compaction.count > 0 ? compactionLabel : "no compactions",
    ];
    if (omittedTools > 0) metaParts.push(`${omittedTools} tools windowed`);
    if (!totalKnown) metaParts.push("full token total is outside the client window");
    return {
      loadedTokens,
      effectiveTokens,
      tokenLimit,
      ratio,
      tone,
      totalKnown,
      loadedMessages,
      totalMessages,
      omittedMessages,
      omittedTools: Number.isFinite(omittedTools) ? omittedTools : 0,
      compaction,
      currentLabel,
      effectiveLabel,
      capLabel: tokenLimit ? formatTokenCount(tokenLimit) : "unset",
      metaLabel: metaParts.join(" | "),
      pipValue: effectiveLabel,
      tooltip: `Current loaded estimate: ${loadedLabel}${
        tokenLimit ? ` / ${formatTokenCount(tokenLimit)} cap` : ""
      }. ${metaParts.join(". ")}.`,
    };
  }, [
    formatTokenCount,
    parseContextLength,
    state.conversation,
    state.conversationTrimMeta,
    state.maxContextLength,
  ]);

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
      void err;
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
        axios.post("/api/history", { sessionId, history }).catch((err) => void err);
      }
    } catch (err) {
      void err;
    }
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
      void err;
      setRuntimeError("Unable to load runtime status.");
    } finally {
      setRuntimeLoading(false);
    }
  }, [backendReady, isLocalMode]);

  const fetchServerRuntimeStatus = React.useCallback(
    async ({ refresh = false } = {}) => {
      const serverUrl =
        typeof state.serverUrl === "string" ? state.serverUrl.trim() : "";
      if (!backendReady || state.backendMode !== "server" || !serverUrl) {
        setServerRuntime(null);
        setServerRuntimeError("");
        setServerRuntimeLoading(false);
        return;
      }
      setServerRuntimeLoading(true);
      setServerRuntimeError("");
      try {
        const res = await axios.get("/api/llm/server/models", {
          params: {
            server_url: serverUrl,
            ...(refresh ? { refresh: true } : {}),
          },
        });
        setServerRuntime(res?.data || null);
      } catch (err) {
        void err;
        setServerRuntime({ reachable: false, models: [] });
        setServerRuntimeError("Server/LAN endpoint is not reachable right now.");
      } finally {
        setServerRuntimeLoading(false);
      }
    },
    [backendReady, state.backendMode, state.serverUrl],
  );

  const fetchProviderSnapshot = React.useCallback(async ({ refresh = false } = {}) => {
    if (!backendReady || !selectedLocalProvider) return;
    try {
      const res = await axios.get("/api/llm/provider/models", {
        params: refresh
          ? { provider: selectedLocalProvider, refresh: true }
          : { provider: selectedLocalProvider },
      });
      applyProviderSnapshot(res?.data || {});
    } catch (err) {
      void err;
      setProviderStatus((prev) => prev);
      setProviderModels((prev) => prev);
    }
  }, [applyProviderSnapshot, backendReady, selectedLocalProvider]);

  const fetchProviderRuntimeStatus = React.useCallback(async () => {
    if (!backendReady || !selectedLocalProvider) return;
    try {
      const res = await axios.get("/api/llm/provider/status", {
        params: { provider: selectedLocalProvider, quick: true },
      });
      setProviderStatus(res?.data?.runtime || null);
    } catch (err) {
      void err;
      setProviderStatus((prev) => prev);
    }
  }, [backendReady, selectedLocalProvider]);

  const fetchProviderLogs = React.useCallback(
    async ({ reset = false } = {}) => {
      if (!backendReady || !selectedLocalProvider) return;
      const cursor = reset ? 0 : providerLogsCursorRef.current;
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
        const resolvedCursor = Number.isFinite(nextCursor) ? nextCursor : cursor;
        providerLogsCursorRef.current = resolvedCursor;
        setProviderLogsCursor(resolvedCursor);
        setProviderLogs((prev) => {
          const merged = reset ? entries : [...prev, ...entries];
          return merged.slice(-500);
        });
      } catch (err) {
        void err;
        if (reset) {
          providerLogsCursorRef.current = 0;
          setProviderLogs([]);
          setProviderLogsCursor(0);
        }
      }
    },
    [backendReady, selectedLocalProvider],
  );

  const runProviderAction = React.useCallback(
    async (endpoint, body = {}, actionName = "action") => {
      if (!backendReady || !selectedLocalProvider) return;
      setProviderPendingAction(actionName);
      setProviderActionError("");
      try {
        await axios.post(endpoint, { provider: selectedLocalProvider, ...body });
        await fetchRuntimeStatus();
        await fetchProviderSnapshot({ refresh: true });
        if (providerLogsOpen) {
          await fetchProviderLogs({ reset: true });
        }
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
      fetchProviderSnapshot,
      fetchRuntimeStatus,
      providerLogsOpen,
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

  const handleProviderSetTarget = React.useCallback(async (modelOverride = null) => {
    if (!backendReady) return;
    const nextModel = String(modelOverride ?? providerSelectedModel ?? "").trim();
    if (!nextModel) return;
    setProviderPendingAction("set-target");
    setProviderActionError("");
    try {
      await axios.post("/api/settings", {
        local_provider_preferred_model: nextModel,
      });
      await fetchRuntimeStatus();
      await fetchProviderSnapshot({ refresh: true });
    } catch (err) {
      const detail =
        err?.response?.data?.detail || "Unable to save the provider target model.";
      setProviderActionError(String(detail));
    } finally {
      setProviderPendingAction("");
    }
  }, [
    backendReady,
    fetchProviderSnapshot,
    fetchRuntimeStatus,
    providerSelectedModel,
  ]);

  const handleProviderModelSelection = React.useCallback(
    (nextModel, { persist = false } = {}) => {
      const normalized = String(nextModel || "").trim();
      setProviderSelectedModel(normalized);
      if (persist && normalized) {
        void handleProviderSetTarget(normalized);
      }
    },
    [handleProviderSetTarget],
  );

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
        const catalogModel = resolveLocalCatalogModelId(modelName);
        const res = await axios.get(
          `/api/models/verify/${encodeURIComponent(catalogModel)}`,
        );
        setModelVerify(res?.data || null);
      } catch (err) {
        void err;
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
      void err;
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
      void err;
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
      void err;
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
            const nextMetadataBase = {
              ...(updatedConversation[mIdx]?.metadata || {}),
              ...(md || {}),
              ...(Object.prototype.hasOwnProperty.call(
                md && typeof md === "object" ? md : {},
                "tool_response_pending",
              )
                ? { tool_response_pending: md.tool_response_pending }
                : { tool_response_pending: false }),
              inline_tool_continuation_pending: false,
              tool_continued: true,
            };
            updatedConversation[mIdx] = {
              ...updatedConversation[mIdx],
              text: joined,
              timestamp: new Date().toISOString(),
              metadata: appendToolContinuationPhase(
                nextMetadataBase,
                existingText,
                continuation,
              ),
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

  const scrollConsoleTargetIntoView = React.useCallback((target, behavior = "smooth") => {
    const body = scrollBodyRef.current;
    if (!target || !body || typeof target.getBoundingClientRect !== "function") {
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior, block: "start" });
      }
      return;
    }
    const bodyRect = body.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const targetTop = targetRect.top - bodyRect.top + body.scrollTop;
    const topInset = 8;
    const nextTop = Math.max(0, targetTop - topInset);
    if (typeof body.scrollTo === "function") {
      body.scrollTo({ top: nextTop, behavior });
    } else {
      body.scrollTop = nextTop;
    }
  }, []);

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
    scrollConsoleTargetIntoView(target, "smooth");
  }, [normalizedFocus, scrollConsoleTargetIntoView]);

  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const entryMatchesTarget = (entry, target) => {
      if (!entry || typeof entry !== "object") return false;
      const entryToolId = String(entry.id ?? entry.request_id ?? "").trim();
      const entryChainId = String(
        entry.chain_id ?? entry.message_id ?? entry.session_id ?? "",
      ).trim();
      const entryAgentId = String(entry.agent_id ?? entry.chain_id ?? entry.session_id ?? "").trim();
      return (
        (entryToolId && target.toolIds.includes(entryToolId)) ||
        (entryChainId &&
          [target.chainId, target.messageId, target.sessionId].includes(entryChainId)) ||
        (entryAgentId && target.agentId && entryAgentId === target.agentId)
      );
    };

  const openMatchingCards = (target) => {
      if (!showToolEntries) return false;
      const matchingAgentIds = new Set();
      displayAgents.forEach((agent) => {
        const agentId = String(agent?.id || "").trim();
        if (!agentId) return;
        if (target.agentId && agentId === target.agentId) {
          matchingAgentIds.add(agentId);
          return;
        }
        const events = Array.isArray(agent?.events) ? agent.events : [];
        if (events.some((entry) => entryMatchesTarget(entry, target))) {
          matchingAgentIds.add(agentId);
        }
      });
      if (!matchingAgentIds.size) return false;
      setCollapsedAgents((prev) => {
        let changed = false;
        const next = { ...prev };
        matchingAgentIds.forEach((id) => {
          if (next[id] === false) return;
          next[id] = false;
          changed = true;
        });
        return changed ? next : prev;
      });
      setExpandedAgents((prev) => {
        let changed = false;
        const next = { ...prev };
        matchingAgentIds.forEach((id) => {
          if (next[id] === true) return;
          next[id] = true;
          changed = true;
        });
        return changed ? next : prev;
      });
      return true;
    };

    const clickMatchingButton = (target, action) => {
      const root = sidebarRef.current;
      if (!root || typeof root.querySelector !== "function") return false;
      const actionSelector = `.tool-action-btn.${action}`;
      const scopes = toolReviewScopeSelectors(target);
      const scopeNodes = scopes
        .map((selector) => {
          try {
            return root.querySelector(selector);
          } catch {
            return null;
          }
        })
        .filter(Boolean);
      scopeNodes.forEach((node) => {
        const closedToggle = node.querySelector?.(
          '.agent-activity-toggle[aria-expanded="false"]',
        );
        if (closedToggle && typeof closedToggle.click === "function") {
          closedToggle.click();
        }
      });
      const selectors = scopes.map((scope) => `${scope} ${actionSelector}`);
      for (const selector of selectors) {
        let button = null;
        try {
          button = root.querySelector(selector);
        } catch {
          button = null;
        }
        if (!button || button.disabled || typeof button.click !== "function") {
          continue;
        }
        button.click();
        return true;
      }
      return false;
    };

    const handleToolReviewAction = (event) => {
      const detail = event?.detail || {};
      if (detail.handled) return;
      const action = normalizeToolReviewAction(detail.action);
      if (!action) return;
      const target = normalizeToolReviewTarget(detail);
      const claimed = openMatchingCards(target);
      let actionCompleted = false;
      const attempt = () => {
        if (actionCompleted) return true;
        if (!clickMatchingButton(target, action)) return false;
        actionCompleted = true;
        detail.handled = true;
        return true;
      };
      if (attempt()) return;
      if (!claimed) {
        if (typeof onRefreshAgents === "function") {
          onRefreshAgents();
        }
        return;
      }
      detail.handled = true;
      if (typeof onRefreshAgents === "function") {
        onRefreshAgents();
      }
      [120, 450, 900].forEach((delay) => {
        window.setTimeout(attempt, delay);
      });
    };

    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewAction);
    return () => {
      window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewAction);
    };
  }, [displayAgents, onRefreshAgents, showToolEntries]);

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
    providerActionPendingRef.current = providerActionPending;
  }, [providerActionPending]);

  React.useEffect(() => {
    providerLogsOpenRef.current = providerLogsOpen;
  }, [providerLogsOpen]);

  React.useEffect(() => {
    if (!backendReady || collapsed) return;
    let runtimeId = null;
    if (isLocalMode) {
      fetchRuntimeStatus();
      if (usingProviderRuntime && !providerActionPendingRef.current) {
        fetchProviderSnapshot();
      }
      runtimeId = setInterval(() => {
        if (usingProviderRuntime && !providerActionPendingRef.current) {
          fetchProviderRuntimeStatus();
          if (providerLogsOpenRef.current) {
            fetchProviderLogs();
          }
        } else {
          fetchRuntimeStatus();
        }
      }, usingProviderRuntime ? PROVIDER_RUNTIME_POLL_MS : LOCAL_RUNTIME_POLL_MS);
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
    fetchProviderSnapshot,
    fetchProviderRuntimeStatus,
    fetchResourceSnapshot,
    fetchRuntimeStatus,
    isLocalMode,
    usingProviderRuntime,
  ]);

  React.useEffect(() => {
    if (!backendReady || collapsed || state.backendMode !== "server") {
      setServerRuntime(null);
      setServerRuntimeError("");
      setServerRuntimeLoading(false);
      return undefined;
    }
    fetchServerRuntimeStatus();
    const serverRuntimeId = setInterval(
      fetchServerRuntimeStatus,
      SERVER_RUNTIME_POLL_MS,
    );
    return () => clearInterval(serverRuntimeId);
  }, [backendReady, collapsed, fetchServerRuntimeStatus, state.backendMode]);

  React.useEffect(() => {
    if (!backendReady || collapsed || !providerLogsOpen || !usingProviderRuntime) return;
    fetchProviderLogs({ reset: true });
  }, [
    backendReady,
    collapsed,
    fetchProviderLogs,
    providerLogsOpen,
    usingProviderRuntime,
  ]);

  React.useEffect(() => {
    if (collapsed) return undefined;
    const timerId = setInterval(() => {
      setRuntimeNow(Date.now());
    }, 1000);
    return () => clearInterval(timerId);
  }, [collapsed]);

  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const clearRuntimeRagTimer = () => {
      if (runtimeRagClearTimerRef.current) {
        clearTimeout(runtimeRagClearTimerRef.current);
        runtimeRagClearTimerRef.current = null;
      }
    };
    const handleRuntimeRagOperation = (event) => {
      const nextOperation = normalizeRuntimeRagOperation(event.detail);
      if (!nextOperation) return;
      clearRuntimeRagTimer();
      setRuntimeRagOperation(nextOperation);
      if (nextOperation.status === "complete" || nextOperation.status === "error") {
        runtimeRagClearTimerRef.current = setTimeout(() => {
          setRuntimeRagOperation((current) =>
            current?.id === nextOperation.id ? null : current,
          );
          runtimeRagClearTimerRef.current = null;
        }, RUNTIME_RAG_OPERATION_CLEAR_MS);
      } else {
        runtimeRagClearTimerRef.current = setTimeout(() => {
          setRuntimeRagOperation((current) =>
            current?.id === nextOperation.id ? null : current,
          );
          runtimeRagClearTimerRef.current = null;
        }, RUNTIME_RAG_OPERATION_STALE_CLEAR_MS);
      }
    };
    window.addEventListener(RUNTIME_RAG_OPERATION_EVENT, handleRuntimeRagOperation);
    return () => {
      window.removeEventListener(
        RUNTIME_RAG_OPERATION_EVENT,
        handleRuntimeRagOperation,
      );
      clearRuntimeRagTimer();
    };
  }, []);

  React.useEffect(() => {
    const modelName = selectedDirectLocalModel;
    if (!modelName) return;
    if (isLocalRuntimeEntry(modelName)) return;
    fetchModelVerify(modelName);
  }, [
    fetchModelVerify,
    selectedDirectLocalModel,
  ]);

  React.useEffect(() => {
    providerLogsCursorRef.current = 0;
    setProviderActionError("");
    setProviderLogs([]);
    setProviderLogsCursor(0);
    setProviderStatus(null);
    setProviderModels([]);
    setProviderSelectedModel("");
    providerAutoSelectedModelRef.current = "";
    if (!usingProviderRuntime) return;
  }, [
    selectedLocalProvider,
    usingProviderRuntime,
  ]);

  const refreshDisabled = !backendReady || (!isCalendar && loadingSnapshot);
  const hiddenCount =
    Object.values(hiddenAgents).filter(Boolean).length +
    (showStandaloneActionHistory && actionHistoryHidden ? 1 : 0) +
    (showSyncInbox && syncInboxHidden ? 1 : 0) +
    (backgroundPanelHidden ? 1 : 0) +
    (runtimePanelHidden ? 1 : 0);
  const hasConversationToolState = React.useMemo(() => {
    const conversation = Array.isArray(state.conversation) ? state.conversation : [];
    return conversation.some(
      (message) =>
        message &&
        Array.isArray(message.tools) &&
        message.tools.some((tool) => tool && typeof tool === "object"),
    );
  }, [state.conversation]);
  const hasInlineToolActivity = React.useMemo(() => {
    if (showToolEntries) return false;
    if (hasConversationToolState) return true;
    return agents.some(
      (agent) =>
        Array.isArray(agent?.events) &&
        agent.events.some((entry) => entry && entry.type === "tool"),
    );
  }, [agents, hasConversationToolState, showToolEntries]);
  const browserSessionContexts = React.useMemo(() => {
    const sessions = new Map();
    displayAgents.forEach((agent) => {
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
  }, [displayAgents]);
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
      setSyncInboxCollapsed(true);
      setSyncInboxHidden(false);
      return;
    }
    if (syncInboxInteractedRef.current) return;
    setSyncInboxCollapsed(true);
  }, [agents.length, hasInlineToolActivity, showSyncInbox]);
  React.useEffect(() => {
    setActionHistoryCollapsed(true);
    setActionHistoryHidden(false);
  }, [showStandaloneActionHistory]);
  React.useEffect(() => {
    syncInboxInteractedRef.current = false;
    setCollapsedChains({});
    setCollapsedToolChats({});
    setCollapsedAgents(() => {
      const next = {};
      (Array.isArray(agents) ? agents : []).forEach((agent) => {
        const key = String(agent?.id || "").trim();
        if (key) next[key] = true;
      });
      return next;
    });
    setExpandedAgents({});
    setHiddenAgents({});
    setActionHistoryCollapsed(true);
    setActionHistoryHidden(false);
    setOpenActionHistoryKey("");
    setActionHistoryFeedback("");
    setSyncReviewFeedback("");
    setSyncInboxCollapsed(true);
    setSyncInboxHidden(false);
    setBackgroundPanelCollapsed(true);
    setBackgroundPanelHidden(false);
    setBackgroundPromptOpen(false);
    setBackgroundComposerMode("prompt");
    setBackgroundPromptDraft("");
    setBackgroundPromptFeedback("");
    setReflectionQuestionDraft("");
    setReflectionMemoryKey("");
    setReflectionFeedback("");
    setRuntimePanelCollapsed(true);
    setRuntimePanelHidden(false);
    setRedirectEditorAgentId("");
    setAgentControlFeedback("");
  }, [state.sessionId]);
  React.useEffect(() => {
    const entries = Array.from(visibleToolChatStats.values()).filter(Boolean);
    if (!entries.length) return;
    const latest = entries.reduce((current, entry) => {
      if (!current) return entry;
      return (Number(entry.latestTimestamp) || 0) >=
        (Number(current.latestTimestamp) || 0)
        ? entry
        : current;
    }, null);
    const latestKey = latest?.chatKey;
    setCollapsedToolChats((prev) => {
      let changed = false;
      const next = { ...prev };
      entries.forEach((entry) => {
        const key = String(entry.chatKey || "").trim();
        if (!key || Object.prototype.hasOwnProperty.call(next, key)) return;
        next[key] = key !== latestKey;
        changed = true;
      });
      return changed ? next : prev;
    });
  }, [visibleToolChatStats]);
  React.useEffect(() => {
    if (backgroundPanelCollapsed) {
      setBackgroundPromptOpen(false);
      setBackgroundComposerMode("prompt");
    }
  }, [backgroundPanelCollapsed]);
  React.useEffect(() => {
    if (!backgroundPanelHidden) return;
    setBackgroundPromptOpen(false);
    setBackgroundComposerMode("prompt");
    setBackgroundPromptDraft("");
    setBackgroundPromptFeedback("");
    setReflectionQuestionDraft("");
    setReflectionMemoryKey("");
    setReflectionFeedback("");
  }, [backgroundPanelHidden]);
  React.useEffect(() => {
    if (!Array.isArray(agents) || !agents.length) return;
    setCollapsedAgents((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const agent of agents) {
        const key = String(agent?.id || "").trim();
        if (!key || Object.prototype.hasOwnProperty.call(next, key)) continue;
        next[key] = true;
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [agents]);
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
      fetchProviderSnapshot();
      if (providerLogsOpen) {
        fetchProviderLogs({ reset: true });
      }
    } else {
      const modelName = selectedDirectLocalModel;
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
    setSyncInboxHidden(false);
    setBackgroundPanelHidden(false);
    setRuntimePanelHidden(false);
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
      setSyncReviewFeedback(
        decision === "approve"
          ? `Applying sync from ${sourceLabel}... This can take a moment while local indexes refresh.`
          : `Rejecting sync from ${sourceLabel}...`,
      );
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

  const toggleSyncInboxHidden = React.useCallback(() => {
    syncInboxInteractedRef.current = true;
    setSyncInboxHidden((prev) => !prev);
  }, []);

  const renderSyncInbox = React.useCallback(() => {
    if (!showSyncInbox || syncInboxHidden) return null;
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

    const pendingSyncCount = pendingSyncReviews.length;
    const recentSyncCount = recentSyncReviews.length;
    const syncInboxFullSubtitle = [
      pendingSyncCount > 0 ? `${pendingSyncCount} pending` : "no pending approvals",
      recentSyncCount > 0 ? `${recentSyncCount} recent` : "",
    ]
      .filter(Boolean)
      .join(" / ");
    const syncInboxSubtitle =
      syncInboxCollapsed && pendingSyncCount === 0 && recentSyncCount > 0
        ? `${recentSyncCount} recent`
        : syncInboxFullSubtitle;

    return (
      <ConsoleObjectCard
        title="sync inbox"
        subtitle={syncInboxSubtitle}
        preview={syncInboxFullSubtitle}
        ariaLabel="sync inbox"
        className="agent-sync-panel"
        collapsed={syncInboxCollapsed}
        onToggleCollapsed={toggleSyncInboxCollapsed}
        onHide={toggleSyncInboxHidden}
        expandLabel="Expand sync inbox"
        collapseLabel="Collapse sync inbox"
        hideLabel="Hide sync inbox"
        extraActions={
          <button
            type="button"
            className="agent-card-control-btn"
            onClick={() => navigate("/knowledge?tab=sync")}
          >
            Open sync
          </button>
        }
      >
        {syncReviewFeedback ? (
          <p className="status-note" role="status">
            {syncReviewFeedback}
          </p>
        ) : null}
        {pendingSyncReviews.length > 0 ? (
          <div className="agent-sync-review-list">
            {pendingSyncReviews.map((review) => renderReviewCard(review, "pending"))}
          </div>
        ) : null}
        {recentSyncReviews.length > 0 ? (
          <div className="agent-sync-review-history">
            <div className="agent-sync-history-label">recent decisions</div>
            <div className="agent-sync-review-list">
              {recentSyncReviews.map((review) => renderReviewCard(review, "recent"))}
            </div>
          </div>
        ) : null}
      </ConsoleObjectCard>
    );
  }, [
    navigate,
    pendingSyncReviews,
    recentSyncReviews,
    showSyncInbox,
    submitSyncReviewDecision,
    syncInboxCollapsed,
    syncInboxHidden,
    syncReviewFeedback,
    syncReviewPendingKey,
    toggleSyncInboxCollapsed,
    toggleSyncInboxHidden,
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
                  {detail?.loading ? <p className="status-note">Loading diff...</p> : null}
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

  const runAgentControl = React.useCallback(
    async (agentId, action, extra = {}) => {
      const normalizedAgentId =
        typeof agentId === "string" ? agentId.trim() : "";
      const normalizedAction =
        typeof action === "string" ? action.trim().toLowerCase() : "";
      if (!normalizedAgentId || !normalizedAction) return;
      const pendingKey = `${normalizedAction}:${normalizedAgentId}`;
      setAgentControlPendingKey(pendingKey);
      setAgentControlFeedback("");
      try {
        await axios.post(`/api/agents/console/${encodeURIComponent(normalizedAgentId)}/${normalizedAction}`, {
          note:
            typeof extra.note === "string" && extra.note.trim() ? extra.note.trim() : "",
          workflow:
            typeof extra.workflow === "string" && extra.workflow.trim()
              ? extra.workflow.trim()
              : "",
        });
        if (normalizedAction === "redirect") {
          setRedirectEditorAgentId("");
          setRedirectNoteDraft("");
          setRedirectWorkflowDraft("");
        }
        await onRefreshAgents?.();
      } catch (err) {
        console.error(`Failed to ${normalizedAction} delegated run`, err);
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          `Failed to ${normalizedAction} delegated run.`;
        setAgentControlFeedback(String(detail));
      } finally {
        setAgentControlPendingKey("");
      }
    },
    [onRefreshAgents],
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
      let model = resolveRequestModelForMode({
        backendMode: mode,
        apiModel: state.apiModel,
        transformerModel: state.transformerModel,
        localModel: state.localModel,
      });
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
      const toolsOverride = Array.isArray(options.tools) ? options.tools.filter(Boolean) : null;
      const messageEntry = conversationById.get(messageId);
      const baseTools = Array.isArray(messageEntry?.tools) ? [...messageEntry.tools] : [];
      const messageKey = String(messageId);
      let accumulatedUpdates =
        toolResolutionUpdatesRef.current.get(messageKey) || [];
      if (toolUpdate) {
        accumulatedUpdates = mergeToolUpdate(accumulatedUpdates, toolUpdate);
        toolResolutionUpdatesRef.current.set(messageKey, accumulatedUpdates);
      }
      const mergedTools = toolsOverride || mergeToolUpdates(baseTools, accumulatedUpdates);
      if (toolUpdate || toolsOverride) {
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
      const continuationLockKey = buildToolContinuationLockKey({
        sessionId,
        messageId,
        tools: batch,
      });
      const continuationLockAcquired =
        !continuationLockKey || acquireToolContinuationLock(continuationLockKey);
      if (!continuationLockAcquired) return;
      toolContinueLocksRef.current.add(messageId);
      try {
        const thinkingPayload = thinkingPayloadForMode(state.thinkingMode);
        const outputTokensPayload = outputTokenPayload(
          state.outputTokenMode,
          state.customOutputTokens,
        );
        const { mode, model, workflow } = resolveContinueTarget(continueTarget);
        const res = await axios.post("/api/chat/continue", {
          session_id: sessionId,
          message_id: messageId,
          model,
          mode,
          workflow,
          ...thinkingPayload,
          ...outputTokensPayload,
          tools: batch,
        });
        toolResolutionUpdatesRef.current.delete(messageKey);
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
        if (continuationLockAcquired) {
          releaseToolContinuationLock(continuationLockKey);
        }
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
      const videoConstraints = state.preferredCameraDeviceId
        ? {
            deviceId: { ideal: state.preferredCameraDeviceId },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            aspectRatio: { ideal: 16 / 9 },
            resizeMode: "none",
          }
        : {
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            aspectRatio: { ideal: 16 / 9 },
            resizeMode: "none",
          };
      stream = await navigator.mediaDevices.getUserMedia({
        video: videoConstraints,
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

  const buildBatchToolUpdate = React.useCallback((entry, status, result, nameOverride, argsOverride) => {
    const name = resolveToolDisplayName(
      { ...entry, name: nameOverride || entry?.name },
      entry?.name || "tool",
    );
    return {
      id: entry?.id || entry?.request_id,
      request_id: entry?.request_id || entry?.id,
      name,
      args: argsOverride ?? entry?.args ?? {},
      ...(typeof result !== "undefined" ? { result } : {}),
      status: status || "invoked",
    };
  }, []);

  const resolveBatchToolDecision = React.useCallback(
    async (entry, decision) => {
      const normalizedDecision = decision === "deny" ? "deny" : "accept";
      const requestId = toolRequestId(entry);
      const toolName = resolveToolDisplayName(entry);
      const sessionForEntry = entry?.session_id || state.sessionId || null;
      const messageForEntry = entry?.message_id || entry?.chain_id || null;
      const targetChain = entry?.chain_id || entry?.message_id || messageForEntry || sessionForEntry;
      const args = entry?.args || {};
      const decisionPayload = {
        request_id: requestId || entry?.id,
        decision: normalizedDecision,
        name: toolName,
        session_id: sessionForEntry,
        message_id: messageForEntry,
        chain_id: targetChain || messageForEntry || sessionForEntry || null,
      };
      if (entry?.args && typeof entry.args === "object") {
        decisionPayload.args = args;
      }

      if (normalizedDecision === "deny") {
        if (requestId) {
          const resp = await axios.post("/api/tools/decision", decisionPayload);
          const status = String(resp?.data?.status || "denied").toLowerCase();
          const result =
            typeof resp?.data?.result !== "undefined"
              ? resp.data.result
              : fallbackResultForStatus(status);
          return buildBatchToolUpdate(entry, status || "denied", result);
        }
        if (entry?.synthetic === true) {
          return buildBatchToolUpdate(
            entry,
            "denied",
            buildToolOutcomeResult("denied", "Dismissed by user."),
          );
        }
        return null;
      }

      if (entry?.manual_fill_required === true && !requestId) return null;

      if (requestId && CLIENT_RESOLUTION_TOOLS.has(toolName)) {
        const resp = await resolveClientTool(entry);
        const status = String(resp?.status || "").toLowerCase();
        const result =
          typeof resp?.result !== "undefined" ? resp.result : fallbackResultForStatus(status);
        return buildBatchToolUpdate(entry, status || "invoked", result);
      }

      if (requestId) {
        const resp = await axios.post("/api/tools/decision", decisionPayload);
        const status = String(resp?.data?.status || "").toLowerCase();
        const result =
          typeof resp?.data?.result !== "undefined"
            ? resp.data.result
            : fallbackResultForStatus(status);
        return buildBatchToolUpdate(entry, status || "invoked", result);
      }

      try {
        const resp = await axios.post("/api/tools/invoke", {
          name: toolName,
          args,
          chain_id: targetChain,
          session_id: sessionForEntry,
          message_id: messageForEntry || targetChain,
        });
        return buildBatchToolUpdate(
          entry,
          "invoked",
          typeof resp?.data?.result !== "undefined" ? resp.data.result : undefined,
        );
      } catch (err) {
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "Tool invoke failed.";
        const statusCode = err?.response?.status;
        const safeDetail = statusCode && statusCode >= 500 ? "Tool error." : detail;
        return buildBatchToolUpdate(
          entry,
          "error",
          buildToolOutcomeResult("error", safeDetail),
        );
      }
    },
    [buildBatchToolUpdate, resolveClientTool, state.sessionId],
  );

  const runToolReviewBatch = React.useCallback(
    async (batch, decision, options = {}) => {
      const entries = Array.isArray(batch?.entries)
        ? batch.entries.filter((entry) => entry?.type === "tool" && isToolAwaitingReview(entry))
        : [];
      const messageId = batch?.messageId || entries[0]?.message_id || entries[0]?.chain_id || null;
      const sessionId = batch?.sessionId || entries[0]?.session_id || state.sessionId || null;
      if (!entries.length || !messageId || !sessionId) return;
      const pendingKey = `${decision}:${sessionId}:${messageId}`;
      setToolBatchPendingKey(pendingKey);
      try {
        const updates = [];
        for (const entry of entries) {
          const update = await resolveBatchToolDecision(entry, decision);
          if (update) updates.push(update);
        }
        const messageEntry = conversationById.get(messageId);
        const sourceTools =
          Array.isArray(messageEntry?.tools) && messageEntry.tools.length
            ? messageEntry.tools
            : entries;
        const mergedTools = mergeToolUpdates(sourceTools, updates);
        await maybeContinueBatch(
          {
            sessionId,
            messageId,
            toolUpdate: null,
          },
          batch?.continueTarget || null,
          {
            force: options.force === true,
            tools: mergedTools,
          },
        );
      } catch (err) {
        console.error("Tool batch action failed", err);
      } finally {
        setToolBatchPendingKey((current) => (current === pendingKey ? "" : current));
      }
    },
    [
      conversationById,
      maybeContinueBatch,
      resolveBatchToolDecision,
      state.sessionId,
    ],
  );

  const openFirstBatchToolEditor = React.useCallback(
    (batch) => {
      const entries = Array.isArray(batch?.entries)
        ? batch.entries.filter((entry) => entry?.type === "tool" && isToolAwaitingReview(entry))
        : [];
      const entry = entries[0];
      if (!entry) return;
      const base =
        state.selectedCalendarDate instanceof Date
          ? new Date(state.selectedCalendarDate)
          : new Date();
      setToolEditorState({
        tool: {
          name: resolveToolDisplayName(entry),
          args: entry.args || {},
          id: entry.id,
          status: entry.status,
        },
        schedulePrefill: {
          start_time: Math.floor(base.getTime() / 1000),
          timezone: preferredTimezone,
          title: `Schedule tool: ${resolveToolDisplayName(entry)}`,
          session_id: batch?.sessionId || entry.session_id || state.sessionId || undefined,
          message_id: batch?.messageId || entry.message_id || entry.chain_id || undefined,
        },
        onSubmit: async ({ args, name, continueTarget }) => {
          const editedEntry = {
            ...entry,
            name: (name || entry.name || "").trim() || entry.name,
            args: args || {},
          };
          const update = await resolveBatchToolDecision(editedEntry, "accept");
          if (!update) return;
          await maybeContinueBatch(
            {
              sessionId: batch?.sessionId || entry.session_id || state.sessionId || null,
              messageId: batch?.messageId || entry.message_id || entry.chain_id || null,
              toolUpdate: update,
            },
            continueTarget || batch?.continueTarget || null,
          );
        },
      });
    },
    [
      maybeContinueBatch,
      preferredTimezone,
      resolveBatchToolDecision,
      state.selectedCalendarDate,
      state.sessionId,
    ],
  );

  const buildPendingToolBatches = React.useCallback(
    (entries) => {
      const groups = new Map();
      (Array.isArray(entries) ? entries : []).forEach((entry) => {
        if (!entry || entry.type !== "tool" || !isToolAwaitingReview(entry)) return;
        const messageId = entry.message_id || entry.chain_id || null;
        const sessionId = entry.session_id || state.sessionId || null;
        if (!messageId || !sessionId) return;
        const key = `${sessionId}:${messageId}`;
        const group = groups.get(key) || {
          key,
          sessionId,
          messageId,
          entries: [],
          continueTarget: null,
        };
        group.entries.push(entry);
        groups.set(key, group);
      });
      return Array.from(groups.values()).filter((group) => group.entries.length > 1);
    },
    [state.sessionId],
  );

  const renderToolBatchActions = (batch) => {
    if (!batch?.entries?.length) return null;
    const busyPrefix = `${batch.sessionId}:${batch.messageId}`;
    const busy = toolBatchPendingKey.endsWith(`:${busyPrefix}`);
    const hasBlockedAccept = batch.entries.some(
      (entry) => entry?.manual_fill_required === true && !toolRequestId(entry),
    );
    const label = summarizeToolBatchLabel(batch.entries);
    const toolIds = batch.entries.map(toolRequestId).filter(Boolean);
    return (
      <div
        key={`tool-batch-actions:${batch.key}`}
        className="agent-tool-batch-actions"
        data-chain-id={batch.messageId ? String(batch.messageId) : undefined}
        data-tool-ids={toolIds.join(" ")}
      >
        <div className="agent-tool-batch-header">
          <span className="agent-tool-batch-kicker">batch</span>
          <span className="agent-tool-batch-label">{batch.entries.length} tools</span>
        </div>
        <div className="agent-tool-batch-controls">
          <button
            type="button"
            className="tool-action-btn accept"
            disabled={busy || hasBlockedAccept}
            aria-label={
              hasBlockedAccept
                ? "One or more tools need editable arguments before the batch can run."
                : `Accept all ${label} and continue the assistant response.`
            }
            onClick={(event) => {
              event.stopPropagation();
              void runToolReviewBatch(batch, "accept");
            }}
          >
            Accept all + continue
          </button>
          <button
            type="button"
            className="tool-action-btn retry"
            disabled={busy || hasBlockedAccept}
            aria-label={
              hasBlockedAccept
                ? "One or more tools need editable arguments before the batch can run."
                : `Accept all ${label} and force a new assistant continuation. Use only to recover a stalled response.`
            }
            title="Recovery action: accept the batch and force a new assistant continuation."
            onClick={(event) => {
              event.stopPropagation();
              void runToolReviewBatch(batch, "accept", { force: true });
            }}
          >
            Accept all + retry answer
          </button>
          <button
            type="button"
            className="tool-action-btn deny"
            disabled={busy}
            aria-label="Deny all pending tools in this batch."
            onClick={(event) => {
              event.stopPropagation();
              void runToolReviewBatch(batch, "deny");
            }}
          >
            Deny
          </button>
          <button
            type="button"
            className="tool-action-btn edit"
            disabled={busy}
            aria-label="Edit the first pending tool in this batch."
            onClick={(event) => {
              event.stopPropagation();
              openFirstBatchToolEditor(batch);
            }}
          >
            Edit
          </button>
        </div>
      </div>
    );
  };

  const renderToolActions = (entry) => {
      const normalizedStatus = getEffectiveToolStatus(entry);
      const awaitingApproval =
        !normalizedStatus || normalizedStatus === "proposed" || normalizedStatus === "pending";
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
      const canRetryResolvedTool = Boolean(
        normalizedStatus &&
          !awaitingApproval &&
          RETRIABLE_TOOL_STATUSES.has(normalizedStatus) &&
          entry.name &&
          targetChain,
      );
      if (
        normalizedStatus &&
        !awaitingApproval &&
        !canContinueResolvedBatch &&
        !canRetryResolvedTool
      ) {
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
      const acceptDisabled = entry?.manual_fill_required === true && !entry?.id;
      const localDenyAllowed = entry?.synthetic === true && !entry?.id;
      const syntheticToolKey =
        typeof entry?.synthetic_id === "string" ? entry.synthetic_id : "";
      const entrySignature = JSON.stringify({
        name: entry?.name || "",
        args:
          entry?.args && typeof entry.args === "object" && !Array.isArray(entry.args)
            ? entry.args
            : {},
      });
      const retryResolvedTool = async (overrideArgs, overrideName, continueTarget) => {
        const toolName = (overrideName || entry.name || "").trim() || entry.name;
        if (!toolName || !targetChain) return;
        const effectiveArgs = overrideArgs ?? entry.args ?? {};
        try {
          const resp = await axios.post("/api/tools/invoke", {
            name: toolName,
            args: effectiveArgs,
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
                name: toolName,
                args: effectiveArgs,
                ...(typeof resp?.data?.result !== "undefined"
                  ? { result: resp.data?.result }
                  : {}),
                status: "invoked",
              },
            },
            continueTarget,
          );
        } catch (err) {
          console.error("Tool retry failed", err);
          const detail =
            err?.response?.data?.detail ||
            err?.response?.data?.message ||
            err?.message ||
            "Tool retry failed.";
          const statusCode = err?.response?.status;
          const safeDetail = statusCode && statusCode >= 500 ? "Tool error." : detail;
          await maybeContinueBatch(
            {
              sessionId: sessionForEntry,
              messageId: messageForEntry,
              toolUpdate: {
                id: entry.id,
                name: toolName,
                args: effectiveArgs,
                result: buildToolOutcomeResult("error", safeDetail),
                status: "error",
              },
            },
            continueTarget,
          );
        }
      };

      if (normalizedStatus && !awaitingApproval) {
        return (
          <div
            className={`agent-tool-actions resolved${
              canContinueResolvedBatch ? " needs-continue" : ""
            }`}
          >
            {canRetryResolvedTool && (
              <>
                <button
                  type="button"
                  className="tool-action-btn retry"
                  title={`Retry ${entry.name || "this tool"} with the same arguments`}
                  onClick={async (event) => {
                    event.stopPropagation();
                    await retryResolvedTool();
                  }}
                >
                  Retry
                </button>
                <button
                  type="button"
                  className="tool-action-btn edit"
                  title={`Edit ${entry.name || "tool"} arguments and retry`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setToolEditorState({
                      tool: {
                        name: entry.name,
                        args: entry.args || {},
                        id: entry.id,
                        status: normalizedStatus || entry.status,
                      },
                      schedulePrefill: (() => {
                        const base =
                          state.selectedCalendarDate instanceof Date
                            ? new Date(state.selectedCalendarDate)
                            : new Date();
                          return {
                            start_time: Math.floor(base.getTime() / 1000),
                            timezone: preferredTimezone,
                            title: `Retry tool: ${entry.name || "tool"}`,
                            session_id: sessionForEntry || undefined,
                            message_id: messageForEntry || undefined,
                          };
                      })(),
                      onSubmit: async ({ args, name, continueTarget }) => {
                        await retryResolvedTool(args, name, continueTarget);
                      },
                    });
                  }}
                >
                  Edit & retry
                </button>
              </>
            )}
            {canContinueResolvedBatch && (
              <button
                type="button"
                className="tool-action-btn continue needs-tool-continue"
                title="Continue the assistant response using the latest resolved tool outcome."
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
            )}
          </div>
        );
      }
      return (
        <div className="agent-tool-actions">
          <button
            type="button"
            className="tool-action-btn accept"
            disabled={acceptDisabled}
            title={
              acceptDisabled
                ? "This tool needs editable arguments before it can run."
                : `Approve and run ${entry.name || "this tool"}`
            }
            onClick={async (event) => {
            event.stopPropagation();
            if (acceptDisabled) return;
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
          disabled={!entry.id && !localDenyAllowed}
          title={
            !entry.id && !localDenyAllowed
              ? "This local fallback tool cannot be denied from the backend."
              : `Deny ${entry.name || "this tool"}`
          }
          onClick={async (event) => {
            event.stopPropagation();
            if (!entry.id && !localDenyAllowed) return;
            try {
              if (entry.id) {
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
              } else if (localDenyAllowed) {
                setState((prev) => {
                  const updated = Array.isArray(prev.conversation)
                    ? [...prev.conversation]
                    : [];
                  const mIdx = updated.findIndex((item) => item && item.id === messageForEntry);
                  if (mIdx === -1) return prev;
                  const msgEntry = { ...(updated[mIdx] || {}) };
                  const tools = Array.isArray(msgEntry.tools) ? [...msgEntry.tools] : [];
                  const tIdx = tools.findIndex((toolEntry) => {
                    if (!toolEntry || typeof toolEntry !== "object") return false;
                    if (syntheticToolKey && toolEntry.synthetic_id === syntheticToolKey) {
                      return true;
                    }
                    return (
                      JSON.stringify({
                        name: toolEntry?.name || "",
                        args:
                          toolEntry?.args &&
                          typeof toolEntry.args === "object" &&
                          !Array.isArray(toolEntry.args)
                            ? toolEntry.args
                            : {},
                      }) === entrySignature
                    );
                  });
                  if (tIdx === -1) return prev;
                  tools[tIdx] = {
                    ...tools[tIdx],
                    status: "denied",
                    result: { status: "denied", message: "Dismissed by user." },
                  };
                  msgEntry.tools = tools;
                  updated[mIdx] = msgEntry;
                  return { ...prev, conversation: updated };
                });
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
          title={`Edit ${entry.name || "tool"} arguments before running`}
          onClick={async (event) => {
            event.stopPropagation();
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
                    session_id: sessionForEntry || undefined,
                    message_id: messageForEntry || undefined,
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
                  const requestId = entry.id ? String(entry.id) : eventId;
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
                        rrule: schedule.rrule,
                        timezone: schedule.timezone,
                        status: schedule.status || "scheduled",
                        background_job: schedule.background_job,
                        actions: [
                          {
                            id: requestId,
                            request_id: requestId,
                            kind: "tool",
                            name: toolName,
                            args: toolArgs,
                            prompt: schedule.prompt,
                            conversation_mode: schedule.conversation_mode,
                            session_id: sessionForEntry || undefined,
                            message_id: messageForEntry || undefined,
                            chain_id: chain,
                          },
                        ],
                      },
                    );
                    await axios.post("/api/tools/schedule", {
                      request_id: requestId,
                      event_id: eventId,
                      name: toolName,
                      args: toolArgs,
                      prompt: schedule.prompt,
                      conversation_mode: schedule.conversation_mode,
                      session_id: sessionForEntry || undefined,
                      message_id: messageForEntry || undefined,
                      chain_id: chain,
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

  const renderToolChatSummaryCard = (summary) => {
    if (!summary) return null;
    const label = summary.label || "chat tools";
    const toolLabel = summary.toolCount === 1 ? "1 tool update" : `${summary.toolCount} tool updates`;
    const cardLabel = `${label}: ${toolLabel} hidden`;
    return (
      <article
        key={`tool-chat-summary-${summary.chatKey}`}
        className="agent-card agent-tool-chat-summary-card"
        style={{ "--agent-chat-color": summary.color }}
        title={cardLabel}
      >
        <button
          type="button"
          className="agent-chat-group-line"
          onClick={() =>
            setCollapsedToolChats((prev) => ({
              ...prev,
              [summary.chatKey]: false,
            }))
          }
          aria-label={`Expand tools from ${label}`}
          title={`Expand tools from ${label}`}
        />
        <div className="agent-tool-chat-summary-copy">
          <div className="agent-tool-chat-summary-header">
            <strong>{label}</strong>
            <span className="agent-status-label">{toolLabel} hidden</span>
          </div>
          {summary.preview ? (
            <p className="agent-activity-preview agent-tool-chat-summary-preview">
              {summary.preview}
            </p>
          ) : null}
        </div>
      </article>
    );
  };

  const renderToolChatGroup = (group) => {
    if (!group?.agents?.length) return null;
    const summary = group.summary || {};
    const label = summary.label || "chat tools";
    const toolLabel =
      summary.toolCount === 1 ? "1 tool update" : `${summary.toolCount || 0} tool updates`;
    return (
      <section
        key={`tool-chat-group-${group.key}`}
        className="agent-tool-chat-group"
        style={{ "--agent-chat-color": summary.color || getThreadColor(group.key) }}
        aria-label={`${label} tools`}
        title={`${label}: ${toolLabel}`}
      >
        <button
          type="button"
          className="agent-chat-group-line"
          onClick={() =>
            setCollapsedToolChats((prev) => ({
              ...prev,
              [group.key]: true,
            }))
          }
          aria-label={`Collapse tools from ${label}`}
          title={`Collapse tools from ${label}`}
        />
        <div className="agent-tool-chat-group-stack">
          {group.agents.map((agent) => renderAgentCard(agent))}
        </div>
      </section>
    );
  };

  const renderAgentCard = (agent) => {
    if (!agent) return null;
    const tone = statusTone(agent.status);
    const filteredActivity = getRenderableAgentActivity(agent, { showToolEntries });
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
    const workflowMeta =
      agent.workflow && typeof agent.workflow === "object" ? agent.workflow : null;
    const provenanceMeta =
      agent.provenance && typeof agent.provenance === "object" ? agent.provenance : null;
    const handoffMeta =
      agent.handoff && typeof agent.handoff === "object" ? agent.handoff : null;
    const controlMeta =
      agent.controls && typeof agent.controls === "object" ? agent.controls : null;
    const workflowLine = formatWorkflowMeta(workflowMeta);
    const provenanceLine = formatProvenanceMeta(provenanceMeta);
    const handoffLine = formatHandoffMeta(handoffMeta);
    const displayLabel = resolveAgentDisplayLabel(agent);
    const toolOnlyAgent = isToolOnlyAgent(agent);
    const conversationTarget = resolveAgentConversationTarget(agent, displayLabel);
    const canOpenConversation =
      !toolOnlyAgent && conversationTarget && typeof onOpenConversation === "function";
    const canOpenConversationAction =
      conversationTarget && typeof onOpenConversation === "function";
    const openConversationFromCard = (event) => {
      event?.stopPropagation?.();
      if (!canOpenConversation) return;
      onOpenConversation(conversationTarget.conversationId, conversationTarget.label);
    };
    const displayLabelKey = normalizePreviewText(displayLabel).toLowerCase();
    const availableControls = Array.isArray(controlMeta?.available)
      ? controlMeta.available.filter((value) => typeof value === "string" && value.trim())
      : [];
    const redirectOpen = agentKeyString && redirectEditorAgentId === agentKeyString;
    const busyControlPrefix = agentKeyString ? `:${agentKeyString}` : "";
    const pauseBusy = agentControlPendingKey === `pause${busyControlPrefix}`;
    const resumeBusy = agentControlPendingKey === `resume${busyControlPrefix}`;
    const stopBusy = agentControlPendingKey === `stop${busyControlPrefix}`;
    const redirectBusy = agentControlPendingKey === `redirect${busyControlPrefix}`;
    const isHidden = !!(agentKeyString && hiddenAgents[agentKeyString]);
    const compactOverride = agentKeyString ? collapsedAgents[agentKeyString] : undefined;
    const isCompact = agentKeyString
      ? compactOverride ?? isBackgroundWorkAgent(agent)
      : false;
    const showAllActivity = !!(agentKeyString && expandedAgents[agentKeyString]);
    const cardHasFocus =
      normalizedFocus &&
      ((normalizedFocus.agentId && agentKeyString === normalizedFocus.agentId) ||
        filteredActivity.some((entry) => matchesFocus(entry)));
    const activeClass = agent.status === "active" ? " active" : "";
    const cardClass = `agent-card${activeClass}${cardHasFocus ? " focused" : ""}${
      isCompact ? " compact" : ""
    }`;
    const orderedActivity = toolOnlyAgent
      ? filteredActivity
      : [...filteredActivity].reverse();
    const activityList = showAllActivity
      ? orderedActivity
      : toolOnlyAgent
        ? orderedActivity.slice(0, 6)
        : filteredActivity.slice(-6).reverse();
    const pendingToolBatches = buildPendingToolBatches(filteredActivity);
    const canExpand = filteredActivity.length > 6;
    const renderedHistoryKeys = new Set();
    const compactMetaNote = isCompact
      ? truncatePreviewText(
          normalizePreviewText(agent.summary || provenanceLine || workflowLine || handoffLine),
          92,
        )
      : "";
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
        onClick={(event) => {
          const target = event.target;
          if (
            typeof Element !== "undefined" &&
            target instanceof Element &&
            target.closest(`${AGENT_CARD_INTERACTIVE_SELECTOR}, .agent-activity`)
          ) {
            return;
          }
          toggleCompact();
        }}
        role={isCompact ? "button" : undefined}
        tabIndex={isCompact ? 0 : undefined}
        onKeyDown={(event) => {
          if (!isCompact) return;
          if (
            typeof Element !== "undefined" &&
            event.target instanceof Element &&
            event.target.closest(AGENT_CARD_INTERACTIVE_SELECTOR)
          ) {
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggleCompact();
          }
        }}
      >
        <header className="agent-card-header">
          <div className="agent-card-meta">
            <span className="agent-status-dot" style={{ backgroundColor: tone.hue }} />
            <div className="agent-card-title-stack">
              <h3 title={displayLabel}>
                {canOpenConversation ? (
                  <button
                    type="button"
                    className="agent-card-title-button"
                    onClick={openConversationFromCard}
                    title={`${conversationTarget.buttonLabel}: ${conversationTarget.label}`}
                  >
                    {displayLabel}
                  </button>
                ) : (
                  displayLabel
                )}
              </h3>
              {compactMetaNote && (
                <span className="agent-card-title-note" title={compactMetaNote}>
                  {compactMetaNote}
                </span>
              )}
            </div>
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
              {availableControls.includes("pause") && (
                <button
                  type="button"
                  className="agent-card-control-btn"
                  onClick={() => runAgentControl(agentKeyString, "pause")}
                  title={controlModeTitle(controlMeta?.modes?.pause)}
                  aria-label="Pause delegated run"
                  disabled={!agentKeyString || Boolean(agentControlPendingKey)}
                >
                  {pauseBusy ? "pausing..." : "pause"}
                </button>
              )}
              {availableControls.includes("resume") && (
                <button
                  type="button"
                  className="agent-card-control-btn"
                  onClick={() => runAgentControl(agentKeyString, "resume")}
                  title={controlModeTitle(controlMeta?.modes?.resume)}
                  aria-label="Resume delegated run"
                  disabled={!agentKeyString || Boolean(agentControlPendingKey)}
                >
                  {resumeBusy ? "resuming..." : "resume"}
                </button>
              )}
              {availableControls.includes("redirect") && (
                <button
                  type="button"
                  className={`agent-card-control-btn${redirectOpen ? " is-active" : ""}`}
                  onClick={() => {
                    if (!agentKeyString) return;
                    setAgentControlFeedback("");
                    setRedirectEditorAgentId((current) =>
                      current === agentKeyString ? "" : agentKeyString,
                    );
                    setRedirectNoteDraft(controlMeta?.redirect_note || "");
                    setRedirectWorkflowDraft(
                      controlMeta?.redirect_workflow || workflowMeta?.id || "",
                    );
                  }}
                  title={controlModeTitle(controlMeta?.modes?.redirect)}
                  aria-label={redirectOpen ? "Close redirect editor" : "Redirect delegated run"}
                  disabled={!agentKeyString || Boolean(agentControlPendingKey)}
                >
                  redirect
                </button>
              )}
              {availableControls.includes("stop") && (
                <button
                  type="button"
                  className="agent-card-control-btn danger"
                  onClick={() => runAgentControl(agentKeyString, "stop")}
                  title={controlModeTitle(controlMeta?.modes?.stop)}
                  aria-label="Request stop for delegated run"
                  disabled={!agentKeyString || Boolean(agentControlPendingKey)}
                >
                  {stopBusy ? "requesting..." : "request stop"}
                </button>
              )}
              {canOpenConversationAction && (
                <button
                  type="button"
                  className="agent-card-control-btn agent-open-chat-btn"
                  onClick={openConversationFromCard}
                  title={`${conversationTarget.buttonLabel}: ${conversationTarget.label}`}
                  aria-label={`${conversationTarget.buttonLabel} ${displayLabel}`}
                >
                  {conversationTarget.buttonLabel}
                </button>
              )}
              {!isCompact && canExpand && (
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
                className={`agent-card-control-btn agent-card-control-symbol${
                  isCompact ? " is-active" : ""
                }`}
                onClick={toggleCompact}
                title={isCompact ? "Expand agent card" : "Compact agent card"}
                aria-label={isCompact ? "Expand agent card" : "Compact agent card"}
                disabled={!agentKeyString}
              >
                {isCompact ? "+" : "-"}
              </button>
              <button
                type="button"
                className="agent-card-control-btn agent-card-control-symbol danger"
                onClick={hideAgent}
                title="Hide agent card"
                aria-label="Hide agent card"
                disabled={!agentKeyString}
              >
                X
              </button>
            </div>
          </div>
        </header>
        {!isCompact && lastMessage && (
          <p className="agent-card-summary" title={lastMessage}>
            {lastMessage}
          </p>
        )}
        {!isCompact && (workflowLine || provenanceLine || handoffLine) && (
          <div className="agent-card-detail-stack">
            {workflowLine && (
              <div className="agent-card-detail-line">
                <span className="agent-resource-pill">workflow</span>
                <span>{workflowLine}</span>
              </div>
            )}
            {provenanceLine && <p className="agent-card-detail-note">{provenanceLine}</p>}
            {handoffLine && <p className="agent-card-detail-note">handoff: {handoffLine}</p>}
          </div>
        )}
        {!isCompact && redirectOpen && (
          <form
            className="agent-card-redirect-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!agentKeyString) return;
              void runAgentControl(agentKeyString, "redirect", {
                note: redirectNoteDraft,
                workflow: redirectWorkflowDraft,
              });
            }}
          >
            <label>
              <span className="agent-card-detail-label">Redirect note</span>
              <textarea
                value={redirectNoteDraft}
                onChange={(event) => setRedirectNoteDraft(event.target.value)}
                rows={2}
                placeholder="What should this delegated run do next?"
              />
            </label>
            <label>
              <span className="agent-card-detail-label">Workflow target</span>
              <input
                type="text"
                value={redirectWorkflowDraft}
                onChange={(event) => setRedirectWorkflowDraft(event.target.value)}
                placeholder="architect_planner, mini_execution, default"
              />
            </label>
            <div className="agent-card-redirect-actions">
              <button
                type="submit"
                className="agent-card-control-btn"
                disabled={!agentKeyString || Boolean(agentControlPendingKey)}
              >
                {redirectBusy ? "sending..." : "send redirect"}
              </button>
              <button
                type="button"
                className="agent-card-control-btn"
                onClick={() => setRedirectEditorAgentId("")}
                disabled={Boolean(agentControlPendingKey)}
              >
                cancel
              </button>
            </div>
          </form>
        )}
        {!isCompact && agentControlFeedback && redirectOpen && (
          <p className="status-note">{agentControlFeedback}</p>
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
              reported tokens
            </span>
          </div>
        )}
        {!isCompact && (
          <div
            className={`agent-tool-activity-region${
              pendingToolBatches.length > 0 ? " has-pending-batch" : ""
            }`}
          >
            {pendingToolBatches.length > 0 && (
              <div className="agent-tool-batch-stack">
                {pendingToolBatches.map((batch) => renderToolBatchActions(batch))}
              </div>
            )}
            <ul className="agent-activity-list">
            {activityList.map((entry) => {
              const ts = formatTimestamp(entry.timestamp);
              const chainIdentifier = entry.chain_id || entry.message_id;
              const isStream = entry.type === "stream";
              const streamMessage = chainIdentifier
                ? conversationById.get(chainIdentifier) ||
                  conversationById.get(String(chainIdentifier)) ||
                  null
                : null;
              const terminalStreamState = isStream
                ? resolveTerminalStreamState(entry, streamMessage)
                : null;
              const status =
                terminalStreamState?.status ||
                getEffectiveToolStatus(entry) ||
                normalizeToolStatus(entry.status) ||
                null;
              const displayStatus = status && status !== "active" ? status : null;
              const toolTone = entry.type === "tool" ? statusTone(status || entry.status || "pending") : null;
              const isProposedTool =
                entry.type === "tool" && (status === "proposed" || status === "pending");
              const isResolvedTool =
                entry.type === "tool" && status && status !== "proposed" && status !== "pending";
              const eventAgeSeconds =
                typeof entry.timestamp === "number" ? Date.now() / 1000 - entry.timestamp : null;
              const entryFocused = matchesFocus(entry);
              const isAged =
                !entryFocused &&
                eventAgeSeconds !== null &&
                Number.isFinite(eventAgeSeconds) &&
                eventAgeSeconds > 180;
              const sourceLabel = (() => {
                const direct = formatModelSourceLabel(entry.mode, entry.model);
                if (direct) return direct;
                if (!chainIdentifier) return "";
                const msg = conversationById.get(chainIdentifier);
                const meta = msg && typeof msg === "object" ? msg.metadata : null;
                return formatModelSourceLabel(meta?.mode, meta?.model);
              })();
              const toolInspectorRows = buildToolStateInspectorRows(entry, {
                agentId: agentKeyString,
                chainIdentifier,
                sessionId: state.sessionId,
                sourceLabel,
                status,
              });
              const collapsed =
                entryFocused
                  ? false
                  : chainIdentifier && Object.prototype.hasOwnProperty.call(collapsedChains, chainIdentifier)
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
              const displayType =
                entry.type === "tool" ? "" : entry.type === "stream" ? "response" : entry.type;
              const streamLabel = isStream ? formatStreamLabel(entry) : null;
              const responseHistory =
                entry.type === "tool"
                  ? actionHistoryByResponse.get(
                    String(entry.message_id || entry.chain_id || "").trim(),
                  ) || null
                  : null;
              const showResponseHistoryToggle = Boolean(
                responseHistory && !renderedHistoryKeys.has(responseHistory.key),
              );
              if (showResponseHistoryToggle) {
                renderedHistoryKeys.add(responseHistory.key);
              }
              const bodyText =
                entry.type === "task"
                  ? entry.content || entry.description || "Task update"
                : isStream
                  ? terminalStreamState && streamLabel === "streaming response"
                    ? terminalStreamState.summary
                    : streamLabel || "streaming response"
                  : entry.content || entry.text || entry.message || "...";
            const preview = collapsed ? buildEntryPreview(entry, bodyText) : null;
            const entryNameKey = normalizePreviewText(entry.name).toLowerCase();
            const showEntryToolName =
              entry.type === "tool"
                ? Boolean(entry.name)
                : entry.name && entryNameKey !== displayLabelKey;
            const handleActivityClick = (event) => {
              const target = event.target;
              const interactiveTarget =
                typeof Element !== "undefined" && target instanceof Element
                  ? target.closest(AGENT_CARD_INTERACTIVE_SELECTOR)
                  : null;
              if (
                interactiveTarget &&
                interactiveTarget !== event.currentTarget
              ) {
                return;
              }
              if (chainIdentifier) {
                toggleCollapsed();
              }
            };
            return (
              <li
                key={activityEntryKey(agent, entry)}
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
                  data-tool-status={entry.type === "tool" && status ? status : undefined}
                  data-agent-id={agentKeyString || undefined}
                  onClick={handleActivityClick}
                  role={chainIdentifier ? "button" : undefined}
                  tabIndex={chainIdentifier ? 0 : undefined}
                  onKeyDown={(event) => {
                    const interactiveTarget =
                      typeof Element !== "undefined" && event.target instanceof Element
                        ? event.target.closest(AGENT_CARD_INTERACTIVE_SELECTOR)
                        : null;
                    if (
                      interactiveTarget &&
                      interactiveTarget !== event.currentTarget
                    ) {
                      return;
                    }
                    if ((event.key === "Enter" || event.key === " ") && chainIdentifier) {
                      event.preventDefault();
                      toggleCollapsed();
                    }
                  }}
                >
                  <div className="agent-activity-meta">
                    {displayType && <span className="agent-activity-type">{displayType}</span>}
                    {showEntryToolName && (
                      <button
                        type="button"
                        className="agent-activity-name agent-activity-name-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleCollapsed();
                        }}
                        title={
                          collapsed
                            ? `Expand ${entry.name} details`
                            : `Collapse ${entry.name} details`
                        }
                        aria-label={
                          collapsed
                            ? `Expand ${entry.name} details`
                            : `Collapse ${entry.name} details`
                        }
                      >
                        {entry.name}
                      </button>
                    )}
                    {displayStatus && entry.type === "tool" ? (
                      <span
                        className="agent-tool-status-code"
                        data-tool-status={status}
                        title={`Tool status: ${displayStatus}`}
                        aria-label={`Tool status: ${displayStatus}`}
                      >
                        <span
                          className="agent-tool-status-pip"
                          aria-hidden="true"
                          style={{ backgroundColor: toolTone?.hue }}
                        />
                        <span className="agent-tool-status-text">{displayStatus}</span>
                      </span>
                    ) : (
                      displayStatus && <span className="agent-activity-status">{displayStatus}</span>
                    )}
                    {entry.type === "tool" && (
                      <StateInspector
                        title="Why this tool is here"
                        summary={
                          isProposedTool
                            ? "The assistant proposed this tool and the console is holding it for review."
                            : "This console row reflects recorded tool activity for this chat turn."
                        }
                        rows={toolInspectorRows}
                        ariaLabel={`Explain ${entry.name || "tool"} activity`}
                      />
                    )}
                    {ts && <time>{ts}</time>}
                    {showResponseHistoryToggle && (
                      <button
                        type="button"
                        className="agent-card-control-btn agent-history-toggle"
                        aria-expanded={openActionHistoryKey === responseHistory.key}
                        aria-label={`Open work history (${responseHistory.actions.length})`}
                        title={`Open work history (${responseHistory.actions.length})`}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleActionHistory(responseHistory);
                        }}
                      >
                        history
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
                        {terminalStreamState ? (
                          <div className="agent-activity-text" role="status">
                            {terminalStreamState.retry}
                          </div>
                        ) : (
                          <div
                            className="agent-stream-progress"
                            role="progressbar"
                            aria-label="Streaming response"
                          />
                        )}
                      </div>
                    ) : (
                      <div className="agent-activity-text">{bodyText}</div>
                    )}
                  </div>
                )}
                  {entry.type === "tool" && (!collapsed || isProposedTool) && renderToolActions(entry)}
                  {entry.type === "tool" &&
                    showResponseHistoryToggle &&
                    renderActionHistoryPopover(responseHistory)}
                </li>
              );
            })}
            </ul>
          </div>
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

    const summarizeActionArguments = (value) => {
      const args =
        value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const keys = Object.keys(args);
      if (!keys.length) return "No argument fields";
      const visibleKeys = keys
        .filter((key) => /^[a-zA-Z_][a-zA-Z0-9_.-]{0,31}$/.test(key))
        .slice(0, 3);
      const hiddenCount = keys.length - visibleKeys.length;
      const countLabel = `${keys.length} argument field${keys.length === 1 ? "" : "s"}`;
      if (!visibleKeys.length) return countLabel;
      return `${countLabel}: ${visibleKeys.join(", ")}${
        hiddenCount > 0 ? ` +${hiddenCount} more` : ""
      }`;
    };

    const summarizeScheduledAction = (action) => {
      const kind = String(action?.kind || action?.type || "").trim().toLowerCase();
      if (kind === "tool") {
        return {
          kind: "Tool",
          name: String(action?.name || "").trim() || "Unnamed tool",
          metadata: summarizeActionArguments(action?.args),
        };
      }
      if (kind === "prompt") {
        return {
          kind: "Prompt",
          name: "Configured prompt",
          metadata: "Text hidden in this card",
        };
      }
      return {
        kind: "Action",
        name: "Scheduled action",
        metadata: "Review to inspect",
      };
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
                } catch (err) {
                  void err;
                }
                return null;
              })();
              const resolvedActions =
                normalizedActions.length > 0
                  ? normalizedActions
                  : parsedTool
                    ? [{ kind: "tool", name: parsedTool.tool, args: parsedTool.args }]
                    : [];
              const hasActions = resolvedActions.length > 0;
              const actionSummaries = resolvedActions.map(summarizeScheduledAction);
              const visibleActionSummaries = actionSummaries.slice(0, 3);
              const hiddenActionCount = actionSummaries.length - visibleActionSummaries.length;
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
                    {hasActions ? (
                      <div className="task-card-action-summary">
                        <ul aria-label="Scheduled actions">
                          {visibleActionSummaries.map((summary, actionIndex) => (
                            <li key={`${key}-action-${actionIndex}`}>
                              <div className="task-card-action-summary-main">
                                <span>{summary.kind}</span>
                                {summary.kind === "Tool" ? (
                                  <code>{summary.name}</code>
                                ) : (
                                  <strong>{summary.name}</strong>
                                )}
                              </div>
                              <small>{summary.metadata}</small>
                            </li>
                          ))}
                        </ul>
                        {hiddenActionCount > 0 ? (
                          <p>
                            +{hiddenActionCount} more action
                            {hiddenActionCount === 1 ? "" : "s"}; Review to inspect.
                          </p>
                        ) : null}
                      </div>
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
    if (runtimePanelHidden) return null;
    const renderContextBudgetPip = (compact = false) =>
      renderConsolePip({
        key: "runtime-context-budget",
        label: "Budget",
        value: contextBudget.pipValue,
        title: contextBudget.tooltip,
        tone: contextBudget.tone,
        compact,
      });
    const renderContextBudgetBlock = () => (
      <div
        className={`runtime-context-budget runtime-context-budget--${contextBudget.tone}`}
        title={contextBudget.tooltip}
      >
        <div className="runtime-meter-row">
          <span className="runtime-meter-label">Context budget</span>
          <span className="runtime-meter-value">{contextBudget.effectiveLabel}</span>
        </div>
        {contextBudget.tokenLimit ? (
          <div className="runtime-meter-bar" aria-hidden="true">
            <div
              className="runtime-meter-fill"
              style={{ width: `${(contextBudget.ratio * 100).toFixed(1)}%` }}
            />
          </div>
        ) : null}
        <div className="runtime-context-budget-grid">
          <span>current</span>
          <strong>{contextBudget.currentLabel}</strong>
          <span>effective</span>
          <strong>{formatTokenCount(contextBudget.effectiveTokens)}</strong>
          <span>cap</span>
          <strong>{contextBudget.capLabel}</strong>
          <span>compacted</span>
          <strong>{contextBudget.compaction.count}</strong>
        </div>
        <div className="runtime-meter-meta">{contextBudget.metaLabel}</div>
      </div>
    );
    const ragOperationStatus = runtimeRagOperation?.status || "";
    const ragOperationActive = Boolean(runtimeRagOperation?.id);
    const ragOperationTone =
      ragOperationStatus === "error"
        ? "error"
        : ragOperationStatus === "complete"
          ? "connected"
          : ragOperationActive
            ? "loading"
            : "idle";
    const ragOperationValue = ragOperationActive
      ? ragOperationStatus === "complete"
        ? "complete"
        : ragOperationStatus === "error"
          ? "error"
          : "retrieving"
      : "idle";
    const renderRagOperationPip = (compact = false) =>
      renderConsolePip({
        key: "runtime-rag-operation",
        label: "Retrieval",
        value: ragOperationValue,
        title: runtimeRagOperation?.phaseLabel || "Chat retrieval status",
        tone: ragOperationTone,
        compact,
      });
    const renderRagOperationBlock = () => {
      if (!runtimeRagOperation) return null;
      const phaseIndex = Number(runtimeRagOperation.phaseIndex);
      const phaseCount = Number(runtimeRagOperation.phaseCount);
      const hasPhaseProgress =
        Number.isFinite(phaseIndex) && Number.isFinite(phaseCount) && phaseCount > 0;
      const progressRatio = hasPhaseProgress
        ? Math.max(0, Math.min(1, phaseIndex / phaseCount))
        : ragOperationStatus === "complete"
          ? 1
          : 1;
      const elapsedMs = Number.isFinite(runtimeRagOperation.elapsedMs)
        ? runtimeRagOperation.elapsedMs
        : runtimeRagOperation.startedAtMs
          ? Math.max(0, runtimeNow - runtimeRagOperation.startedAtMs)
          : null;
      const returnedMatches = Number(runtimeRagOperation.counts?.returned_matches);
      const requestedTopK = Number(runtimeRagOperation.counts?.requested_top_k);
      const clipTopK = Number(runtimeRagOperation.counts?.clip_top_k);
      const metaParts = [];
      if (hasPhaseProgress) {
        metaParts.push(`Step ${Math.max(1, phaseIndex)} of ${Math.max(1, phaseCount)}`);
      }
      if (elapsedMs !== null) metaParts.push(formatOperationElapsed(elapsedMs));
      if (Number.isFinite(returnedMatches)) {
        metaParts.push(`${returnedMatches} matches`);
      } else {
        const requestedParts = [];
        if (Number.isFinite(requestedTopK) && requestedTopK > 0) {
          requestedParts.push(`${requestedTopK} text`);
        }
        if (Number.isFinite(clipTopK) && clipTopK > 0) {
          requestedParts.push(`${clipTopK} vision`);
        }
        if (requestedParts.length) metaParts.push(`top ${requestedParts.join(" + ")}`);
      }
      return (
        <div
          className={`runtime-rag-progress runtime-rag-progress--${ragOperationTone}`}
          role="status"
          aria-live="polite"
          title={runtimeRagOperation.detail || runtimeRagOperation.phaseLabel}
        >
          <div className="runtime-meter-row">
            <span className="runtime-meter-label">Retrieval</span>
            <span className="runtime-meter-value">{ragOperationValue}</span>
          </div>
          {runtimeRagOperation.phaseLabel ? (
            <div className="runtime-rag-progress-phase">
              {runtimeRagOperation.phaseLabel}
            </div>
          ) : null}
          <div className="runtime-meter-bar" aria-hidden="true">
            <div
              className={`runtime-meter-fill${
                !hasPhaseProgress && ragOperationStatus !== "complete"
                  ? " is-indeterminate"
                  : ""
              }`}
              style={
                !hasPhaseProgress && ragOperationStatus !== "complete"
                  ? undefined
                  : { width: `${(progressRatio * 100).toFixed(1)}%` }
              }
            />
          </div>
          <div className="runtime-meter-meta">
            {metaParts.join(" | ")}
            {metaParts.length > 0 && runtimeRagOperation.detail ? " | " : ""}
            {runtimeRagOperation.detail}
          </div>
        </div>
      );
    };
    const renderRuntimeContractPips = (contract, compact = false) => {
      const laneLabel = RUNTIME_LANE_LABELS[contract.lane] || contract.lane;
      const operationStatus = String(contract.lastOperation?.status || "")
        .trim()
        .toLowerCase();
      const operationTone = ["error", "failed"].includes(operationStatus)
        ? "error"
        : ["pending", "running", "starting", "stopping"].includes(operationStatus)
          ? "loading"
          : "task";
      return (
        <div
          className={
            compact
              ? "agent-console-pip-row agent-console-pip-row--compact"
              : "agent-console-pip-row"
          }
          data-runtime-lane={contract.lane}
          data-runtime-availability={contract.availability}
        >
          {!compact
            ? renderConsolePip({
                key: "runtime-contract-lane",
                label: "Lane",
                value: laneLabel,
                title: `Runtime lane: ${laneLabel}`,
                tone:
                  contract.lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER
                    ? "provider"
                    : "runtime",
                compact,
              })
            : null}
          {renderConsolePip({
            key: "runtime-contract-model",
            label: "Model",
            value: contract.model || "not selected",
            title: contract.model
              ? `Runtime model: ${contract.model}`
              : "No runtime model selected",
            tone: contract.model ? "connected" : "idle",
            compact,
          })}
          {contract.endpoint && !compact
            ? renderConsolePip({
                key: "runtime-contract-endpoint",
                label: "Endpoint",
                value: contract.endpoint,
                title: `Runtime endpoint: ${contract.endpoint}`,
                tone: runtimeAvailabilityTone(contract.availability),
                compact,
              })
            : null}
          {contract.lastOperation
            ? renderConsolePip({
                key: "runtime-contract-operation",
                label: "Operation",
                value: contract.lastOperation.label,
                title: contract.lastOperation.title,
                tone: operationTone,
                compact,
              })
            : null}
          {renderContextBudgetPip(compact)}
          {renderRagOperationPip(compact)}
        </div>
      );
    };
    if (modeLabel !== "local") {
      const serverMode = String(modeLabel).toLowerCase() === "server";
      const apiModel = state.apiModel || "api model not selected";
      const transformerModel = state.transformerModel || "transformer model not selected";
      const serverModel = serverRuntime?.loaded_model || state.transformerModel || "server default";
      const serverUrl = state.serverUrl || "server url not set";
      const wsStatus = normalizeStatusValue(state.wsStatus);
      const wsLabel =
        wsStatus === "online"
          ? "connected"
          : wsStatus === "loading"
            ? "connecting"
            : wsStatus === "degraded"
              ? "degraded"
              : "offline";
      const runtimeContract = resolveRuntimePanelContract(
        {
          mode: serverMode ? "server" : "api",
          apiStatus: state.apiStatus,
          apiProviderStatus: state.apiProviderStatus,
          apiModel: state.apiModel,
          serverUrl: state.serverUrl,
          transformerModel: state.transformerModel,
          serverRuntime,
          serverModels: serverRuntime?.models,
          serverLoadedModel: serverRuntime?.loaded_model,
          serverLoading: serverMode && serverRuntimeLoading,
          serverError: serverMode ? serverRuntimeError : "",
        },
        runtimeNow,
      );
      const laneLabel = RUNTIME_LANE_LABELS[runtimeContract.lane];

      return (
        <ConsoleObjectCard
          title="runtime"
          subtitle={laneLabel}
          preview={
            serverMode
              ? `Server ${serverUrl}; Model ${serverModel}; ${runtimeContract.availability}; Budget ${contextBudget.pipValue}`
              : `API ${apiModel}; Transformer ${transformerModel}; ${runtimeContract.availability}; Budget ${contextBudget.pipValue}`
          }
          className="agent-runtime-panel"
          collapsed={runtimePanelCollapsed}
          onToggleCollapsed={() => setRuntimePanelCollapsed((prev) => !prev)}
          onHide={() => setRuntimePanelHidden(true)}
          expandLabel="Expand runtime details"
          collapseLabel="Collapse runtime details"
          hideLabel="Hide runtime"
          controlButtonClassName="runtime-action-btn"
          symbolButtonClassName="runtime-action-symbol"
          extraActions={
            serverMode ? (
              <button
                type="button"
                className="runtime-action-btn"
                onClick={() => fetchServerRuntimeStatus({ refresh: true })}
                disabled={serverRuntimeLoading}
                aria-label="Refresh Server/LAN runtime status"
                title="Refresh Server/LAN runtime status"
              >
                {serverRuntimeLoading ? "checking" : "refresh"}
              </button>
            ) : null
          }
          status={
            <div
              className="runtime-panel-status"
              title={`runtime availability: ${runtimeContract.availability}`}
              data-runtime-availability={runtimeContract.availability}
            >
              {runtimeContract.availability}
            </div>
          }
          collapsedContent={renderRuntimeContractPips(runtimeContract, true)}
        >
          {renderRuntimeContractPips(runtimeContract, false)}
          {renderContextBudgetBlock()}
          {renderRagOperationBlock()}
          <div className="runtime-panel-note runtime-panel-summary" role="status">
            {serverMode
              ? `Server/LAN is ${runtimeContract.availability}, using ${serverModel} via ${serverUrl}. WebSocket is ${wsLabel}.`
              : `Cloud API is ${runtimeContract.availability}, using ${apiModel} with ${transformerModel}. WebSocket is ${wsLabel}.`}
          </div>
        </ConsoleObjectCard>
      );
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
      const providerLabel = formatLocalRuntimeLabel(selectedLocalProvider);
      const effectiveProviderModel =
        typeof providerRuntime.effective_model_id === "string"
          ? providerRuntime.effective_model_id.trim()
          : typeof providerRuntime.effective_model === "string"
            ? providerRuntime.effective_model.trim()
            : "";
      const loadedModel =
        typeof providerRuntime.loaded_model === "string"
          ? providerRuntime.loaded_model.trim()
          : "";
      const embeddingOnlyLoadedModel = Boolean(loadedModel) && !isChatCapableProviderModelName(loadedModel);
      const providerModelOptions = Array.from(
        new Set(
          [
            ...providerModels,
            isChatCapableProviderModelName(effectiveProviderModel) ? effectiveProviderModel : "",
            isChatCapableProviderModelName(loadedModel) ? loadedModel : "",
            providerSelectedModel,
          ]
            .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
            .filter((entry) => isChatCapableProviderModelName(entry)),
        ),
      );
      const selectedProviderModel = isChatCapableProviderModelName(providerSelectedModel)
        ? providerSelectedModel.trim()
        : "";
      const effectiveSelectedModel =
        (
          selectedProviderModel ||
          effectiveProviderModel ||
          providerModelOptions[0] ||
          ""
        ).trim();
      const contextSupported = capabilities.context_length !== false;
      const externalEndpointMode = capabilities.start_stop === false;
      const loadControlsAvailable = capabilities.load_unload !== false;
      const providerLogsSupported = capabilities.logs_stream !== false;
      const startStopAvailable = capabilities.start_stop !== false;
      const providerEndpointReachable =
        serverRunning || (externalEndpointMode && providerModelOptions.length > 0);
      const providerRuntimeLastError =
        typeof providerRuntime.last_error === "string"
          ? providerRuntime.last_error.trim()
          : "";
      const baseUrl =
        typeof providerRuntime.base_url === "string" ? providerRuntime.base_url : "";
      const serverOwnershipKnown =
        typeof providerRuntime.server_owned_by_float === "boolean";
      const serverOwnedByFloat = serverOwnershipKnown
        ? providerRuntime.server_owned_by_float
        : true;
      const loadedModelOwnershipKnown =
        typeof providerRuntime.loaded_model_owned_by_float === "boolean";
      const loadedModelOwnedByFloat = loadedModelOwnershipKnown
        ? providerRuntime.loaded_model_owned_by_float
        : serverOwnedByFloat;
      const externalManagedServer =
        !externalEndpointMode &&
        serverRunning &&
        serverOwnershipKnown &&
        !serverOwnedByFloat;
      const externalManagedLoadedModel =
        Boolean(loadedModel) &&
        loadedModelOwnershipKnown &&
        !loadedModelOwnedByFloat;
      const providerOwnershipWarning =
        externalManagedServer || externalManagedLoadedModel
          ? `${providerLabel || selectedLocalProvider} is already running outside Float${
              baseUrl ? ` at ${baseUrl}` : ""
            }. Stop it in the external app or switch this lane to External HTTP only before using start, stop, load, or unload here.`
          : "";
      const providerStatusLabel = modelLoaded
        ? externalManagedLoadedModel
          ? "external model loaded"
          : "model loaded"
        : embeddingOnlyLoadedModel
          ? externalManagedLoadedModel
            ? "external embedding loaded"
            : "embedding loaded"
          : externalEndpointMode
            ? providerEndpointReachable
              ? "endpoint reachable"
              : "endpoint offline"
            : externalManagedServer
              ? "external server running"
            : serverRunning
              ? "server running"
              : installed
                ? "installed"
                : "not installed";
      const providerDetails =
        providerRuntime?.details && typeof providerRuntime.details === "object"
          ? providerRuntime.details
          : {};
      const contextLength =
        typeof providerRuntime.context_length === "number"
          ? providerRuntime.context_length
          : null;
      const providerCheckedAgo = formatRuntimeRelativeTime(
        providerRuntime?.checked_at,
        runtimeNow,
      );
      const providerCheckedClock = formatRuntimeClockTime(providerRuntime?.checked_at);
      const providerCheckTooltip = providerCheckedAgo
        ? `Inventory checked ${providerCheckedAgo}${
            providerCheckedClock ? ` (${providerCheckedClock})` : ""
          }. Automatic provider refresh runs about once per minute.`
        : "Inventory has not been checked yet.";
      const providerLastOperation = formatProviderLastOperation(
        providerRuntime?.last_operation,
        runtimeNow,
      );
      const runtimeLastError =
        providerActionError ||
        (
          externalEndpointMode &&
          /remote load endpoint is unavailable/i.test(providerRuntimeLastError)
            ? ""
            : providerRuntimeLastError
        ) ||
        runtimeError ||
        "";
      const runtimeContract = resolveRuntimePanelContract(
        {
          mode: "local",
          localModel: selectedLocalProvider,
          providerMode: providerRuntime?.mode,
          providerRuntime,
          providerModels,
          providerModel: effectiveSelectedModel,
          providerPreferredModel: providerSelectedModel,
          providerLoading: runtimeLoading || providerActionPending,
          providerError: runtimeLastError,
        },
        runtimeNow,
      );
      const providerRuntimeInspectorRows = buildProviderRuntimeInspectorRows({
        providerKey: selectedLocalProvider,
        providerLabel: providerLabel || selectedLocalProvider,
        providerRuntime,
        status: providerStatusLabel,
        summary: providerStatusLabel,
        detail: runtimeLastError,
        ownershipWarning: providerOwnershipWarning,
        lastOperation: providerLastOperation,
        actionMessage: providerActionError,
      });
      const providerFreshnessTone = runtimeLastError
        ? "error"
        : externalManagedServer || externalManagedLoadedModel
          ? "warn"
        : modelLoaded
          ? "ok"
          : externalEndpointMode
            ? providerEndpointReachable
              ? "ok"
              : "idle"
            : embeddingOnlyLoadedModel || serverRunning || installed
              ? "warn"
            : "idle";
      const logsCount = Array.isArray(providerLogs) ? providerLogs.length : 0;
      const loadBusy = providerPendingAction === "load";
      const unloadBusy = providerPendingAction === "unload";
      const startBusy = providerPendingAction === "start";
      const stopBusy = providerPendingAction === "stop";
      const setTargetBusy = providerPendingAction === "set-target";
      const controlsLocked = loadBusy || unloadBusy || setTargetBusy;
      const showProviderInventory =
        providerModelOptions.length > 0 || Boolean(effectiveSelectedModel);
      const providerInventoryCount = providerModelOptions.length;
      const providerInventoryLabel =
        providerInventoryCount === 1
          ? "1 model"
          : providerInventoryCount > 1
            ? `${providerInventoryCount} models`
            : "";
      const detailSizeBytesCandidates = [
        providerDetails?.model_size_bytes,
        providerDetails?.size_bytes,
        providerDetails?.vram_estimate_bytes,
        providerDetails?.memory_bytes,
      ];
      const detailSizeBytes = detailSizeBytesCandidates.find(
        (value) => typeof value === "number" && Number.isFinite(value) && value > 0,
      );
      const selectedModelMetaParts = [];
      const modelSizeLabel =
        typeof detailSizeBytes === "number"
          ? formatBytes(detailSizeBytes)
          : formatModelScaleLabel(effectiveSelectedModel);
      if (modelSizeLabel) {
        selectedModelMetaParts.push(modelSizeLabel);
      }
      const detailQuantLabel =
        typeof providerDetails?.quantization === "string" && providerDetails.quantization.trim()
          ? providerDetails.quantization.trim()
          : typeof providerDetails?.quant === "string" && providerDetails.quant.trim()
            ? providerDetails.quant.trim()
            : "";
      const modelQuantLabel = detailQuantLabel || formatModelQuantLabel(effectiveSelectedModel);
      if (modelQuantLabel && selectedModelMetaParts.length < 2) {
        selectedModelMetaParts.push(modelQuantLabel);
      }
      if (
        contextLength &&
        effectiveSelectedModel &&
        (effectiveSelectedModel === effectiveProviderModel || effectiveSelectedModel === loadedModel) &&
        selectedModelMetaParts.length < 2
      ) {
        selectedModelMetaParts.push(`ctx ${formatTokenCount(contextLength)}`);
      }
      const selectedModelMetaLabel = selectedModelMetaParts.join(" / ");
      const showProviderActionRow =
        startStopAvailable || showProviderInventory || loadControlsAvailable;
      const showProviderSecondaryRow =
        (contextSupported && loadControlsAvailable) || providerLogsSupported;

      return (
        <ConsoleObjectCard
          title="runtime"
          subtitle={providerLabel || "local runtime"}
          className="agent-runtime-panel"
          collapsed={runtimePanelCollapsed}
          preview={`${providerLabel || selectedLocalProvider}: ${runtimeContract.availability}; ${providerStatusLabel}${
            loadedModel ? `; loaded ${loadedModel}` : ""
          }`}
          onToggleCollapsed={() => setRuntimePanelCollapsed((prev) => !prev)}
          onHide={() => setRuntimePanelHidden(true)}
          expandLabel="Expand runtime details"
          collapseLabel="Collapse runtime details"
          hideLabel="Hide runtime"
          controlButtonClassName="runtime-action-btn"
          symbolButtonClassName="runtime-action-symbol"
          extraActions={
            <>
              <button
                type="button"
                className="runtime-action-btn"
                onClick={() => {
                  fetchRuntimeStatus();
                  fetchProviderSnapshot({ refresh: true });
                  if (providerLogsOpen) {
                    fetchProviderLogs({ reset: true });
                  }
                }}
                disabled={providerActionPending && providerPendingAction === "refresh"}
                aria-label="Refresh provider runtime status"
                title="Refresh provider runtime status"
              >
                refresh
              </button>
              <span
                className={`runtime-freshness-indicator ${providerFreshnessTone}`}
                title={providerCheckTooltip}
                aria-label="Provider inventory freshness"
              />
              <StateInspector
                title="Why this runtime state is shown"
                summary="Provider runtime status combines bridge inventory, ownership checks, and the last action result."
                rows={providerRuntimeInspectorRows}
                ariaLabel="Explain provider runtime state"
              />
            </>
          }
          status={
            <div
              className="runtime-panel-status"
              title={`runtime availability: ${runtimeContract.availability}`}
              data-runtime-availability={runtimeContract.availability}
            >
              {runtimeContract.availability}
            </div>
          }
          collapsedContent={renderRuntimeContractPips(runtimeContract, true)}
        >
          {renderRuntimeContractPips(runtimeContract, false)}
          <div className="runtime-model-row">
            <span className="runtime-model-name" title={providerLabel || selectedLocalProvider}>
              {providerLabel || selectedLocalProvider}
            </span>
            {externalEndpointMode ? (
              <span className="runtime-pill" title="Float is inspecting an external provider endpoint.">
                remote endpoint
              </span>
            ) : null}
            {baseUrl ? <span className="runtime-pill">{baseUrl}</span> : null}
            {loadedModel ? (
              <span className="runtime-pill" title="loaded provider model">
                loaded {loadedModel}
              </span>
            ) : null}
            {providerInventoryLabel ? (
              <span className="runtime-pill" title="provider inventory">
                {providerInventoryLabel}
              </span>
            ) : null}
            {contextLength ? (
              <span className="runtime-pill" title="loaded context length">
                ctx {formatTokenCount(contextLength)}
              </span>
            ) : null}
          </div>
          {renderContextBudgetBlock()}
          {renderRagOperationBlock()}
          {providerRuntime?.model_mismatch ? (
            <div className="runtime-panel-warning" role="status">
              Loaded model {loadedModel || "unknown"} differs from preferred model{" "}
              {providerRuntime.preferred_model || "unknown"}.
            </div>
          ) : null}
          {embeddingOnlyLoadedModel ? (
            <div className="runtime-panel-warning" role="status">
              Loaded model {loadedModel} looks like an embedding model. Chat requests need a
              language model loaded here.
            </div>
          ) : null}
          {providerOwnershipWarning ? (
            <div className="runtime-panel-warning" role="status">
              {providerOwnershipWarning}
            </div>
          ) : null}
          {providerLastOperation ? (
            <div className="runtime-panel-note" role="status" title={providerLastOperation.title}>
              {providerLastOperation.label}
            </div>
          ) : null}
          {showProviderActionRow ? (
            <>
              <div className="runtime-provider-actions">
                {startStopAvailable ? (
                  <>
                    <button
                      type="button"
                      className="runtime-action-btn"
                      onClick={handleProviderStart}
                      disabled={startBusy}
                      title="Start provider server"
                    >
                      {startBusy ? "starting..." : "start"}
                    </button>
                    <button
                      type="button"
                      className="runtime-action-btn"
                      onClick={handleProviderStop}
                      disabled={stopBusy}
                      title="Stop provider server"
                    >
                      {stopBusy ? "stopping..." : "stop"}
                    </button>
                  </>
                ) : null}
                {showProviderInventory ? (
                  <div
                    className={`runtime-provider-select-wrap${
                      selectedModelMetaLabel ? " has-meta" : ""
                    }`}
                  >
                    <select
                      className="model-select runtime-provider-model-select"
                      value={effectiveSelectedModel}
                      onChange={(event) =>
                        handleProviderModelSelection(event.target.value, {
                          persist: externalEndpointMode,
                        })
                      }
                      disabled={controlsLocked || providerModelOptions.length === 0}
                      title={externalEndpointMode ? "Provider target model" : "Provider model"}
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
                    {selectedModelMetaLabel ? (
                      <span className="runtime-provider-model-meta" title="Model metadata hint">
                        {selectedModelMetaLabel}
                      </span>
                    ) : null}
                  </div>
                ) : null}
                {loadControlsAvailable ? (
                  <>
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
                  </>
                ) : null}
              </div>
              {showProviderSecondaryRow ? (
                <div className="runtime-provider-actions">
                  {contextSupported && loadControlsAvailable ? (
                    <>
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
                        disabled={controlsLocked}
                        placeholder="optional"
                        title="Optional context length for load requests"
                      />
                    </>
                  ) : null}
                  {providerLogsSupported ? (
                    <button
                      type="button"
                      className="runtime-action-btn"
                      onClick={() => setProviderLogsOpen((prev) => !prev)}
                      disabled={false}
                      title="Show or hide provider runtime logs"
                    >
                      {providerLogsOpen ? "hide logs" : "show logs"} ({logsCount})
                    </button>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : null}
          {runtimeLastError ? (
            <div className="runtime-panel-error" role="status">
              {runtimeLastError}
            </div>
          ) : null}
          {providerLogsOpen && providerLogsSupported ? (
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
        </ConsoleObjectCard>
      );
    }
    const modelName = selectedDirectLocalModel;
    const activeModelId =
      (typeof runtime?.effective_model_id === "string" && runtime.effective_model_id.trim()) ||
      (typeof runtime?.model === "string" && runtime.model.trim()) ||
      "";
    const activeModelDiffers =
      Boolean(activeModelId) &&
      Boolean(modelName) &&
      normalizeModelId(activeModelId) !== normalizeModelId(modelName);
    const loadState = runtime?.load_state || "idle";
    const isLoaded = runtime?.loaded || loadState === "ready";
    const loadError = runtime?.load_error || runtimeError;
    const runtimePreflight =
      runtime?.preflight && typeof runtime.preflight === "object"
        ? runtime.preflight
        : null;
    const runtimeTiming = (() => {
      const startedAgo = formatRuntimeRelativeTime(runtime?.load_started_at, runtimeNow);
      const finishedAgo = formatRuntimeRelativeTime(runtime?.load_finished_at, runtimeNow);
      const finishedClock = formatRuntimeClockTime(runtime?.load_finished_at);
      if (loadState === "loading" && startedAgo) {
        return `Loading started ${startedAgo}.`;
      }
      if (loadState === "ready" && finishedAgo) {
        return `Loaded ${finishedAgo}${finishedClock ? ` (${finishedClock})` : ""}.`;
      }
      if (loadState === "error" && finishedAgo) {
        return `Load failed ${finishedAgo}${finishedClock ? ` (${finishedClock})` : ""}.`;
      }
      return null;
    })();
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
      return null;
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

    const runtimeContract = resolveRuntimePanelContract(
      {
        mode: "local",
        localRuntimeKind: "direct",
        localModel: modelName,
        runtime,
        localLoading: runtimeLoading || loadPending || unloadPending,
        localError: !backendReady ? "Backend unavailable" : loadError,
      },
      runtimeNow,
    );
    const statusText = runtimeContract.availability;

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
    const directRuntimeSummary = (
      <div className="runtime-panel-note runtime-panel-summary" role="status">
        <div className="runtime-model-row">
          <span
            className="runtime-model-name"
            title={hasModel ? `local model: ${modelName}` : "no local model selected"}
          >
            {hasModel ? modelName : "local model"}
          </span>
          {activeModelDiffers ? (
            <span className="runtime-pill" title={`loaded model: ${activeModelId}`}>
              loaded {activeModelId}
            </span>
          ) : null}
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
      </div>
    );
    return (
      <ConsoleObjectCard
        title="runtime"
        subtitle={modeLabel === "local" ? "local inference" : `mode: ${modeLabel}`}
        className="agent-runtime-panel"
        collapsed={runtimePanelCollapsed}
        preview={`${hasModel ? modelName : "local model"}; ${statusText}${
          tokenLimit ? `; ctx ${formatTokenCount(tokenLimit)}` : ""
        }`}
        onToggleCollapsed={() => setRuntimePanelCollapsed((prev) => !prev)}
        onHide={() => setRuntimePanelHidden(true)}
        expandLabel="Expand runtime details"
        collapseLabel="Collapse runtime details"
        hideLabel="Hide runtime"
        controlButtonClassName="runtime-action-btn"
        symbolButtonClassName="runtime-action-symbol"
        extraActions={
          modeLabel === "local" ? (
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
          ) : null
        }
        status={
          <div
            className="runtime-panel-status"
            title={`runtime availability: ${statusText}`}
            data-runtime-availability={statusText}
          >
            {statusText}
          </div>
        }
        collapsedContent={
          <>
            {renderRuntimeContractPips(runtimeContract, true)}
            {directRuntimeSummary}
          </>
        }
      >
        {renderRuntimeContractPips(runtimeContract, false)}
        <div className="runtime-model-row">
          <span
            className="runtime-model-name"
            title={hasModel ? `local model: ${modelName}` : "no local model selected"}
          >
            {hasModel ? modelName : "local model"}
          </span>
          {activeModelDiffers ? (
            <span className="runtime-pill" title={`loaded model: ${activeModelId}`}>
              loaded {activeModelId}
            </span>
          ) : null}
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
        {renderContextBudgetBlock()}
        {renderRagOperationBlock()}
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
        {runtimeTiming ? (
          <div className="runtime-panel-note" role="status">
            {runtimeTiming}
          </div>
        ) : null}
        {runtimePreflight?.python_executable ? (
          <div className="runtime-panel-note" role="status">
            Backend Python: {runtimePreflight.python_executable}
          </div>
        ) : null}
        {runtimePreflight?.missing_packages?.length ? (
          <div className="runtime-panel-error" role="status">
            Missing direct-local packages: {runtimePreflight.missing_packages.join(", ")}.
          </div>
        ) : null}
        {runtimePreflight?.missing_runtime_components?.length ? (
          <div className="runtime-panel-error" role="status">
            Missing transformers loader classes:{" "}
            {runtimePreflight.missing_runtime_components.join(", ")}.
          </div>
        ) : null}
        {!loadError && runtimePreflight?.hint ? (
          <div className="runtime-panel-note" role="status">
            {runtimePreflight.hint}
          </div>
        ) : null}
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
      </ConsoleObjectCard>
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
          handleUnifiedPress(event, () => onToggle?.())
        }
        onPointerDown={(event) =>
          handleUnifiedPress(event, () => onToggle?.())
        }
        aria-label="Collapse agent console"
        title="Collapse agent console"
      >
        {">"}
      </button>
      <div className="sidebar-header right-header">
        <div className="right-header-title-row">
          <button
            className={`stream-toggle ${streamEnabled ? "on" : "off"}`}
            onClick={onStreamToggle}
            title={streamEnabled ? "Pause console stream" : "Resume console stream"}
            aria-pressed={streamEnabled}
          >
            {streamEnabled ? "pause" : "resume"}
          </button>
          <h2>agent console</h2>
        </div>
        <div className="right-header-controls-scroll history-controls-scroll">
          <div className="history-controls-scroll-content right-header-controls-scroll-content">
            <div className="console-permission-control">
              <label htmlFor="console-permission-select">tool approval</label>
              <select
                id="console-permission-select"
                className="console-permission-select"
                value={state.approvalLevel}
                onChange={handleApprovalLevelChange}
                title="Choose when Float asks before running tools"
                aria-label="Tool approval mode"
              >
                <option value="all">Review all</option>
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
                hidden ({hiddenCount})
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
        </div>
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
        {renderBackgroundPanel()}
        {isCalendar && renderCalendar()}
        {hasInlineToolActivity && (
          <p className="agent-console-note" role="status">
            Tool details are inline in chat. The console is showing thoughts, messages, and tasks
            only.
          </p>
        )}
        {backendReady ? (
          visibleAgentItems.length === 0 ? (
            <p className="agent-console-empty">
              {loadingSnapshot ? "Loading agents..." : "No active agents yet."}
            </p>
          ) : (
            visibleAgentItems.map((item) =>
              item.type === "tool-chat-summary"
                ? renderToolChatSummaryCard(item.summary)
                : item.type === "tool-chat-group"
                  ? renderToolChatGroup(item)
                  : renderAgentCard(item.agent),
            )
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
