import React, { useState, useEffect, useContext, useRef, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createPortal } from "react-dom"; // fix: portal used without import
import "../styles/Chat.css";
import "../styles/ToolActions.css";
import "../styles/ToolPayload.css";
import MediaViewer from "./MediaViewer";
import BrowserSessionDialog from "./BrowserSessionDialog";
import RagContextPanel, { normalizeRagMatches } from "./RagContextPanel";
import axios from "axios";
import { Room, RoomEvent } from "livekit-client";
import { memoryStore, apiWrapper } from "../utils/proxy";
import { ensureDeviceAndToken } from "../utils/sync";
import { GlobalContext } from "../main";
import DOMPurify from "dompurify";
import { marked } from "marked";
import IconButton from "@mui/material/IconButton";
import TextField from "@mui/material/TextField";
import Button from "@mui/material/Button";
import Tooltip from "@mui/material/Tooltip";
import Divider from "@mui/material/Divider";
import ToolEditorModal from "./ToolEditorModal";
import ToolPayloadView, {
  extractComputerPayload,
  summarizeToolPayload,
} from "./ToolPayloadView";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import MicIcon from "@mui/icons-material/Mic";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import VolumeUpIcon from "@mui/icons-material/VolumeUp";
import PauseCircleFilledIcon from "@mui/icons-material/PauseCircleFilled";
import PhotoCameraIcon from "@mui/icons-material/PhotoCamera";
import ScreenShareIcon from "@mui/icons-material/ScreenShare";
import TuneIcon from "@mui/icons-material/Tune";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import { normalizeToolDisplayMode } from "../utils/toolDisplayModes";
import {
  buildToolContinuationSignature,
  hasMatchingToolContinuationSignature,
} from "../utils/toolContinuations";
import { mergeContinuationText } from "../utils/continuationText";

const DEFAULT_COMPOSER_ROWS = 4;
const MAX_COMPOSER_ROWS = 72;
const EMPTY_GLOBAL_STATE = Object.freeze({
  conversation: [],
  history: [],
});
const NOOP_SET_STATE = () => {};
const TOOL_PLACEHOLDER_RE = /\[\[tool_call:(\d+)\]\]/g;
const VISION_WORKFLOW_OPTIONS = [
  {
    value: "auto",
    label: "auto",
    description: "Let the model choose the best visual reasoning path for the attached image.",
  },
  {
    value: "image_qa",
    label: "q&a",
    description: "Focus on answering questions about the image instead of describing everything in it.",
  },
  {
    value: "ocr",
    label: "ocr",
    description: "Treat the image like a document and prioritize reading visible text.",
  },
  {
    value: "compare",
    label: "compare",
    description: "Compare two or more attached images and call out similarities or differences.",
  },
  {
    value: "caption",
    label: "caption",
    description: "Generate a clean description of the attached image.",
  },
];
const VISION_WORKFLOW_FIELD_DESCRIPTION =
  "How the image will be interpreted by the model.";
const SAFE_REALTIME_TOOL_NAMES = [
  "remember",
  "recall",
  "tool_help",
  "tool_info",
  "search_web",
];
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const LIVE_TOOL_PANEL_OFFSETS = {
  camera: 0,
  mic: 44,
  volume: 88,
  thinking: 132,
};
const LIVE_SESSION_CANCELLED_CODE = "LIVE_SESSION_CANCELLED";

const createLiveSessionCancelledError = () => {
  const error = new Error("Live streaming start was cancelled.");
  error.code = LIVE_SESSION_CANCELLED_CODE;
  return error;
};

const isLiveSessionCancelledError = (error) =>
  error &&
  (error.code === LIVE_SESSION_CANCELLED_CODE ||
    error.message === "Live streaming start was cancelled.");

const normalizeRealtimeToolSchema = (schema) => {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return {};
  }
  const normalized = { ...schema };
  if (normalized.properties && typeof normalized.properties === "object") {
    normalized.properties = Object.fromEntries(
      Object.entries(normalized.properties).map(([key, value]) => [
        key,
        normalizeRealtimeToolSchema(value),
      ]),
    );
  }
  if (normalized.items && typeof normalized.items === "object") {
    normalized.items = normalizeRealtimeToolSchema(normalized.items);
  }
  if (Array.isArray(normalized.anyOf)) {
    normalized.anyOf = normalized.anyOf.map((entry) =>
      normalizeRealtimeToolSchema(entry),
    );
  }
  if (Array.isArray(normalized.oneOf)) {
    normalized.oneOf = normalized.oneOf.map((entry) =>
      normalizeRealtimeToolSchema(entry),
    );
  }
  if (Array.isArray(normalized.type) && normalized.type.includes("array") && !normalized.items) {
    normalized.items = {};
  }
  if (normalized.type === "array" && !normalized.items) {
    normalized.items = {};
  }
  return normalized;
};

const toValidDate = (value) => {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

const getDateKey = (date) =>
  `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;

export const formatMessageTimestampLabel = (timestamp, previousTimestamp = null) => {
  const currentDate = toValidDate(timestamp);
  if (!currentDate) return "";
  const timeLabel = currentDate.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const previousDate = toValidDate(previousTimestamp);
  if (!previousDate || getDateKey(previousDate) === getDateKey(currentDate)) {
    return timeLabel;
  }
  const dateOptions = {
    month: "short",
    day: "numeric",
  };
  if (currentDate.getFullYear() !== new Date().getFullYear()) {
    dateOptions.year = "numeric";
  }
  const dateLabel = currentDate.toLocaleDateString([], dateOptions);
  return `${dateLabel} · ${timeLabel}`;
};

const formatMessageTimestampTitle = (timestamp) => {
  const date = toValidDate(timestamp);
  return date
    ? date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
    : "";
};

const getRequestErrorDetail = (error, fallback = "Request failed") => {
  const data = error?.response?.data;
  const detail = data?.detail || data?.message || data?.error;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  const status = error?.response?.status;
  if (status === 502) {
    return "Request failed (502). The backend or dev proxy was unavailable for a moment.";
  }
  const message = typeof error?.message === "string" ? error.message.trim() : "";
  return message || fallback;
};

const LIVE_STREAM_INPUT_TRANSCRIPT_DELTA_TYPES = new Set([
  "conversation.item.input_audio_transcription.delta",
  "input_audio_transcription.delta",
]);

const LIVE_STREAM_INPUT_TRANSCRIPT_DONE_TYPES = new Set([
  "conversation.item.input_audio_transcription.completed",
  "input_audio_transcription.completed",
]);

const LIVE_STREAM_ASSISTANT_TRANSCRIPT_DELTA_TYPES = new Set([
  "response.audio_transcript.delta",
  "response.output_audio_transcript.delta",
  "response.text.delta",
]);

const LIVE_STREAM_ASSISTANT_TRANSCRIPT_DONE_TYPES = new Set([
  "response.audio_transcript.done",
  "response.output_audio_transcript.done",
  "response.text.done",
]);

const collectRealtimeContentStrings = (value) => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const next = [];
    if (typeof entry.transcript === "string") next.push(entry.transcript);
    if (typeof entry.text === "string") next.push(entry.text);
    if (typeof entry.delta === "string") next.push(entry.delta);
    if (Array.isArray(entry.content)) {
      next.push(...collectRealtimeContentStrings(entry.content));
    }
    return next;
  });
};

const extractRealtimeTranscriptText = (payload) => {
  if (!payload || typeof payload !== "object") return "";
  const responseOutput = Array.isArray(payload.response?.output)
    ? payload.response.output
    : [];
  const candidates = [
    payload.transcript,
    payload.delta,
    payload.text,
    payload.audio_transcript,
    payload.item?.transcript,
    payload.item?.text,
    payload.response?.transcript,
    payload.response?.text,
    payload.response?.output_text,
    ...collectRealtimeContentStrings(payload.item?.content),
    ...collectRealtimeContentStrings(payload.response?.content),
    ...collectRealtimeContentStrings(responseOutput),
  ];
  const match = candidates.find((value) => typeof value === "string");
  return typeof match === "string" ? match : "";
};

const createClientMessageId = (prefix = "msg") => {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

const getLiveStreamingStatusLabel = (phase) => {
  switch (phase) {
    case "connecting":
      return "connecting";
    case "user-speaking":
      return "listening";
    case "transcribing":
      return "transcribing";
    case "assistant-thinking":
      return "thinking";
    case "assistant-speaking":
      return "responding";
    case "listening":
      return "live";
    default:
      return "idle";
  }
};

const escapeHtml = (value) =>
  String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const extractInlineToolPayloads = (metadata) => {
  if (!metadata || typeof metadata !== "object") return [];
  const payloads = Array.isArray(metadata.inline_tool_payloads)
    ? metadata.inline_tool_payloads.filter((item) => typeof item === "string")
    : [];
  if (!payloads.length && typeof metadata.inline_tool_payload === "string") {
    return [metadata.inline_tool_payload];
  }
  return payloads;
};

const parseInlineToolPayload = (raw) => {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const name = parsed.tool;
    const args = parsed.params || parsed.arguments || parsed.args || {};
    if (typeof name !== "string" || !name.trim() || typeof args !== "object") {
      return null;
    }
    return { name: name.trim(), args };
  } catch {
    return null;
  }
};

const HARMONY_TAG_RE = /<\|[^|>]+?\|>/g;

const stripHarmonyEnvelope = (value) => {
  if (typeof value !== "string") return "";
  let text = value.replace(/\r\n/g, "\n");
  if (!text.includes("<|")) return text;
  const messageTag = "<|message|>";
  const messageIndex = text.lastIndexOf(messageTag);
  if (messageIndex !== -1) {
    text = text.slice(messageIndex + messageTag.length);
  }
  text = text.replace(HARMONY_TAG_RE, " ");
  text = text.replace(
    /\b(?:channel|commentary|constrain|message)\b(?:\s*to=[^\s]+)?/gi,
    " ",
  );
  text = text.replace(/\bto=[^\s]+\b/gi, " ");
  return text;
};

const formatDuration = (seconds = 0) => {
  if (!Number.isFinite(seconds)) return "0:00";
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const secs = String(total % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
};

const collapseTokenizedLines = (value) => {
  if (typeof value !== "string") return "";
  const lines = value.split("\n");
  if (lines.length < 6) return value;
  const trimmed = lines.map((line) => line.trim()).filter(Boolean);
  if (trimmed.length < 6) return value;
  const avgLength =
    trimmed.reduce((sum, line) => sum + line.length, 0) / trimmed.length;
  const shortLines = trimmed.filter((line) => line.length <= 3).length;
  const shortRatio = shortLines / trimmed.length;
  if (avgLength < 12 || shortRatio > 0.6) {
    return trimmed.join(" ");
  }
  return value;
};

const normalizeThoughtText = (value) => {
  const stripped = stripHarmonyEnvelope(value);
  const collapsed = collapseTokenizedLines(stripped);
  return collapsed.replace(/\s+/g, " ").trim();
};

const appendThoughtChunk = (thoughts, chunk) => {
  if (typeof chunk !== "string" || chunk.length === 0) {
    return Array.isArray(thoughts) ? thoughts : [];
  }
  const list = Array.isArray(thoughts) ? [...thoughts] : [];
  if (!list.length) {
    list.push(chunk);
    return list;
  }
  list[list.length - 1] = `${list[list.length - 1]}${chunk}`;
  return list;
};

const toolSignature = (tool) => {
  if (!tool) return "";
  const name = typeof tool.name === "string" ? tool.name.trim() : "";
  if (!name) return "";
  const args = tool.args && typeof tool.args === "object" ? tool.args : {};
  try {
    return JSON.stringify({ name, args });
  } catch {
    return name;
  }
};

const normalizeToolEntry = (tool) => {
  if (!tool) return null;
  if (typeof tool === "string") {
    const name = tool.trim();
    if (!name) return null;
    return { name, args: {}, status: "proposed" };
  }
  if (typeof tool !== "object") return null;
  const name =
    typeof tool.name === "string"
      ? tool.name.trim()
      : typeof tool.tool === "string"
        ? tool.tool.trim()
        : "";
  if (!name) return null;
  const args =
    tool.args && typeof tool.args === "object"
      ? tool.args
      : tool.params && typeof tool.params === "object"
        ? tool.params
        : tool.arguments && typeof tool.arguments === "object"
          ? tool.arguments
          : {};
  return {
    ...tool,
    name,
    args,
    status: tool.status || "proposed",
  };
};

const stripJsonFence = (value) => {
  if (typeof value !== "string") return value;
  const match = value.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (!match) return value;
  return match[1]?.trim() || value;
};

const coerceJsonish = (value) => {
  if (typeof value !== "string") return value;
  let normalized = value.trim();
  if (!normalized) return value;
  normalized = normalized
    .replace(/\bNone\b/g, "null")
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false");
  if (normalized.includes("'")) {
    normalized = normalized
      .replace(/([{,]\s*)'([^']+?)'\s*:/g, '$1"$2":')
      .replace(/:\s*'([^']*?)'/g, ': "$1"');
  }
  return normalized;
};

const parseToolJson = (value) => {
  if (typeof value !== "string") return value;
  let trimmed = value.trim();
  if (!trimmed) return value;
  trimmed = stripJsonFence(trimmed);

  if (trimmed.startsWith("\"") && trimmed.endsWith("\"")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed === "string") {
        return parseToolJson(parsed);
      }
      return parsed;
    } catch {
      // fall through
    }
  }

  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    try {
      const coerced = coerceJsonish(trimmed);
      return JSON.parse(coerced);
    } catch {
      return value;
    }
  }
};

const unwrapToolOutcome = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { payload: value, message: null };
  }
  const hasStatus = typeof value.status === "string" && value.status.trim();
  const hasWrapperKeys = "data" in value || "ok" in value || "message" in value;
  if (!hasStatus || !hasWrapperKeys) {
    return { payload: value, message: null };
  }
  return {
    payload: Object.prototype.hasOwnProperty.call(value, "data") ? value.data : value,
    message: typeof value.message === "string" ? value.message : null,
  };
};

const normalizeToolPayload = (value) => {
  const parsed = parseToolJson(value);
  const { payload } = unwrapToolOutcome(parsed);
  return parseToolJson(payload);
};

const formatToolPayload = (value) => {
  const normalized = normalizeToolPayload(value);
  if (normalized === null || typeof normalized === "undefined") return "";
  if (typeof normalized === "string") return normalized;
  try {
    return JSON.stringify(normalized, null, 2);
  } catch {
    return String(normalized);
  }
};

const summarizeToolPayloadValue = (value, toolName) => {
  if (value === null || typeof value === "undefined") return "";
  const toolLabel = typeof toolName === "string" ? toolName.toLowerCase() : "";
  if (toolLabel.startsWith("computer.") || toolLabel === "open_url") {
    return summarizeToolPayload(value, toolName);
  }
  const parsed = parseToolJson(value);
  const { payload, message } = unwrapToolOutcome(parsed);
  if (message) return message;
  const normalized = parseToolJson(payload);
  if (toolLabel.includes("search") && normalized && typeof normalized === "object") {
    const query = normalized.query || normalized.search || normalized.q || "";
    const results = Array.isArray(normalized.results) ? normalized.results : null;
    const firstTitle =
      results && results.length
        ? results[0]?.title || results[0]?.name || results[0]?.label || ""
        : "";
    if (query && firstTitle) return `Search: "${query}" -> ${firstTitle}`;
    if (query) return `Search: "${query}"`;
  }
  if (normalized && typeof normalized === "object") {
    if (normalized.key) return `key: ${normalized.key}`;
    if (normalized.title) return `title: ${normalized.title}`;
    if (normalized.name) return String(normalized.name);
    if (normalized.message) return String(normalized.message);
  }
  if (typeof normalized === "string") return normalized;
  try {
    return JSON.stringify(normalized);
  } catch {
    return String(normalized);
  }
};

const mergeInlineTools = (tools, metadata) => {
  const base = (Array.isArray(tools) ? tools : [])
    .map(normalizeToolEntry)
    .filter(Boolean);
  const signatures = new Set(base.map(toolSignature).filter(Boolean));
  const payloads = extractInlineToolPayloads(metadata);
  payloads.forEach((raw) => {
    const parsed = parseInlineToolPayload(raw);
    if (!parsed) return;
    const entry = normalizeToolEntry({
      name: parsed.name,
      args: parsed.args || {},
      status: "proposed",
    });
    const sig = toolSignature(entry);
    if (!sig || signatures.has(sig)) return;
    signatures.add(sig);
    base.push(entry);
  });
  return base;
};

const resolveMessageTools = (msg) =>
  mergeInlineTools(msg?.tools, msg?.metadata);

const normalizeToolResultPayload = (value) => {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
};

const getBrowserSessionConversationContext = (msg, tool, order = 0) => {
  if (!tool || typeof tool !== "object") return null;
  const computer = extractComputerPayload(
    normalizeToolResultPayload(tool.result),
    tool.name,
  );
  const sessionId =
    computer?.sessionId ||
    (typeof tool.args?.session_id === "string" ? tool.args.session_id.trim() : "");
  if (!sessionId) return null;
  return {
    ...computer,
    sessionId,
    messageId: msg?.id || msg?.message_id || null,
    chainId: msg?.id || msg?.message_id || null,
    sessionKey:
      (typeof msg?.session_id === "string" && msg.session_id) ||
      (typeof msg?.sessionId === "string" && msg.sessionId) ||
      null,
    tool,
    message: msg,
    order,
  };
};

export const mergeToolEntries = (
  existing,
  incoming,
  metadata,
  options = {},
) => {
  const { includeInlineMetadata = true } = options || {};
  const base = (Array.isArray(existing) ? existing : [])
    .map(normalizeToolEntry)
    .filter(Boolean);
  const merged = [...base];
  const additions = includeInlineMetadata
    ? mergeInlineTools(incoming, metadata)
    : (Array.isArray(incoming) ? incoming : [])
        .map(normalizeToolEntry)
        .filter(Boolean);
  additions.forEach((tool) => {
    const normalized = normalizeToolEntry(tool);
    if (!normalized) return;
    const rawId = normalized.id || normalized.request_id || null;
    const toolId = rawId ? String(rawId) : null;
    let idx = -1;
    if (toolId) {
      idx = merged.findIndex(
        (entry) =>
          entry &&
          typeof entry === "object" &&
          (String(entry.id || entry.request_id || "") === toolId),
      );
    }
    if (idx === -1) {
      const sig = toolSignature(normalized);
      if (sig) {
        idx = merged.findIndex((entry) => toolSignature(entry) === sig);
      }
    }
    if (idx >= 0) {
      merged[idx] = { ...merged[idx], ...normalized };
    } else {
      merged.push(normalized);
    }
  });
  return merged;
};

const normalizeToolStatus = (status) => {
  const raw = typeof status === "string" ? status.trim().toLowerCase() : "";
  if (!raw) return "";
  if (["ok", "success", "succeeded", "complete", "completed"].includes(raw)) {
    return "invoked";
  }
  if (["failed", "failure"].includes(raw)) return "error";
  if (raw === "rejected") return "denied";
  if (raw === "canceled") return "cancelled";
  if (raw === "timed_out") return "timeout";
  return raw;
};

const getToolResultStatus = (result) => {
  const parsed = parseToolJson(result);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
  const status = normalizeToolStatus(parsed.status);
  if (status) return status;
  if (parsed.data && typeof parsed.data === "object" && !Array.isArray(parsed.data)) {
    return normalizeToolStatus(parsed.data.status);
  }
  return "";
};

const getEffectiveToolStatus = (tool) => {
  if (!tool || typeof tool !== "object") return "";
  const status = normalizeToolStatus(tool.status);
  if (status && status !== "proposed" && status !== "pending") {
    return status;
  }
  return getToolResultStatus(tool.result) || status;
};

const getToolStatusDisplay = (status, statusRaw = "") => {
  switch (status) {
    case "invoked":
      return { tone: "invoked", label: "done", glyph: "ok" };
    case "denied":
      return { tone: "denied", label: "denied", glyph: "no" };
    case "cancelled":
      return { tone: "cancelled", label: "cancelled", glyph: "stop" };
    case "timeout":
      return { tone: "timeout", label: "timeout", glyph: "late" };
    case "error":
      return { tone: "error", label: "error", glyph: "err" };
    case "scheduled":
      return { tone: "scheduled", label: "scheduled", glyph: "at" };
    case "proposed":
    case "pending":
      return { tone: "pending", label: "pending", glyph: "..." };
    default: {
      const cleaned = (statusRaw || status || "pending").trim().toLowerCase();
      return { tone: cleaned || "pending", label: cleaned || "pending", glyph: "..." };
    }
  }
};

const isToolReadyForContinue = (tool) => {
  if (!tool || typeof tool !== "object") return false;
  const status = getEffectiveToolStatus(tool);
  if (!status || status === "proposed" || status === "pending") return false;
  const hasResult = typeof tool.result !== "undefined" && tool.result !== null;
  if (hasResult) return true;
  return (
    status === "denied" ||
    status === "error" ||
    status === "cancelled" ||
    status === "canceled" ||
    status === "timeout"
  );
};

const buildToolContinuationBatch = (tools) => {
  const normalized = (Array.isArray(tools) ? tools : [])
    .map(normalizeToolEntry)
    .filter(Boolean);
  if (!normalized.length) return null;
  if (!normalized.every(isToolReadyForContinue)) return null;
  return normalized;
};

const formatModelSourceLabel = (mode, model) => {
  const safeMode = typeof mode === "string" ? mode.trim() : "";
  const safeModel = typeof model === "string" ? model.trim() : "";
  if (safeMode && safeModel) return `${safeMode}:${safeModel}`;
  if (safeModel) return safeModel;
  if (safeMode) return safeMode;
  return "";
};

const resolveModeModel = (mode, state) => {
  const currentMode = (mode || state.backendMode || "").toLowerCase();
  if (currentMode === "local") {
    return { mode: currentMode, model: state.localModel || state.transformerModel || "" };
  }
  if (currentMode === "server") {
    return { mode: currentMode, model: state.transformerModel || state.apiModel || "" };
  }
  if (currentMode === "api") {
    return { mode: currentMode, model: state.apiModel || "" };
  }
  return { mode: currentMode || state.backendMode, model: state.apiModel || state.transformerModel || state.localModel || "" };
};

const getMessageStatusBadge = (msg) => {
  if (!msg || typeof msg !== "object") return null;
  const meta = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
  if (meta.unresolved_tool_loop) {
    return {
      label: "partial",
      tone: "warn",
      title: "Tool follow-up used fallback output. Review tool outcomes and continue if needed.",
    };
  }
  const status = typeof meta.status === "string" ? meta.status.trim().toLowerCase() : "";
  if (status === "error") {
    return { label: "error", tone: "error", title: "Generation ended with an error." };
  }
  if (status === "cancelled" || status === "canceled") {
    return {
      label: "stopped",
      tone: "muted",
      title: "Generation was stopped before completion.",
    };
  }
  return null;
};

const mergeThoughtChunks = (thoughts) => {
  const chunks = (Array.isArray(thoughts) ? thoughts : []).filter(
    (item) => typeof item === "string" && item.length,
  );
  if (chunks.length <= 1) return chunks;
  const hasHarmonyTokens = chunks.some(
    (item) => item.includes("<|") || item.includes("|>"),
  );
  if (hasHarmonyTokens) {
    return [chunks.join("")];
  }
  const lengths = chunks.map((item) => item.trim().length);
  const avgLength = lengths.reduce((sum, len) => sum + len, 0) / lengths.length;
  const shortRatio = lengths.filter((len) => len <= 3).length / lengths.length;
  if (chunks.length > 6 && (avgLength < 8 || shortRatio > 0.6)) {
    return [chunks.join("")];
  }
  return chunks;
};

const buildThoughtBlocks = (thoughts) => {
  const chunks = mergeThoughtChunks(thoughts);
  const blocks = [];
  chunks.forEach((item) => {
    const stripped = stripHarmonyEnvelope(item);
    if (!stripped.trim()) return;
    const collapsed = collapseTokenizedLines(stripped);
    const normalized = collapsed.replace(/\n{3,}/g, "\n\n");
    normalized.split(/\n{2,}/).forEach((part) => {
      const trimmed = part.trim();
      if (trimmed) blocks.push(trimmed);
    });
  });
  return blocks;
};

const ragMatchesFromSection = (section) => {
  if (!section) return [];
  if (Array.isArray(section)) return normalizeRagMatches(section);
  if (section && Array.isArray(section.matches)) {
    return normalizeRagMatches(section.matches);
  }
  return [];
};

const getMessageRagMatches = (msg) => {
  if (!msg || typeof msg !== "object") return [];
  if (Array.isArray(msg.ragMatches) && msg.ragMatches.length) {
    return msg.ragMatches;
  }
  if (Array.isArray(msg.rag)) {
    return normalizeRagMatches(msg.rag);
  }
  if (msg.metadata && msg.metadata.rag) {
    return ragMatchesFromSection(msg.metadata.rag);
  }
  return [];
};

const Chat = ({
    thoughts = [],
    activeMessageId,
    setActiveMessageId,
    messageDelta,
    onOpenConsole,
  }) => {
  const globalContext = useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const navigate = useNavigate();
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null);
  // attachments: [{ id, file, url }]
  const [attachments, setAttachments] = useState([]);
  const [visionWorkflow, setVisionWorkflow] = useState("auto");
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [screenCaptureBusy, setScreenCaptureBusy] = useState(false);
  const [liveVisualMode, setLiveVisualMode] = useState("off");
  const [liveVisualError, setLiveVisualError] = useState("");
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [chatSettingsSection, setChatSettingsSection] = useState("camera");
  const [availableInputDevices, setAvailableInputDevices] = useState({
    audioinput: [],
    videoinput: [],
  });
  const [micTestActive, setMicTestActive] = useState(false);
  const [micTestLevel, setMicTestLevel] = useState(0);
  const [chatSettingsPopoverStyle, setChatSettingsPopoverStyle] = useState(null);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const fileInputRef = useRef(null);
  const cameraVideoRef = useRef(null);
  const cameraStreamRef = useRef(null);
  const liveVisualPreviewRef = useRef(null);
  const liveVisualStreamRef = useRef(null);
  const liveVisualSenderRef = useRef(null);
  const liveVisualPublicationRef = useRef(null);
  const liveVisualTrackRef = useRef(null);
  const voiceSourceStreamRef = useRef(null);
  const voiceAudioContextRef = useRef(null);
  const voiceGainNodeRef = useRef(null);
  const realtimeToolStateRef = useRef({});
  const realtimePendingToolCallsRef = useRef(new Set());
  const realtimeConfiguredToolsRef = useRef(false);
  const chatSettingsMenuRef = useRef(null);
  const chatSettingsTriggerRef = useRef(null);
  const chatSettingsPopoverRef = useRef(null);
  const attachmentMenuRef = useRef(null);
  const realtimeResponseLifecycleRef = useRef({
    active: false,
    requested: false,
  });
  const realtimeTurnDetectionRef = useRef({
    type: "server_vad",
    interrupt_response: true,
  });
  const micTestRef = useRef({
    rawStream: null,
    processedStream: null,
    audioContext: null,
    analyser: null,
    rafId: null,
  });
  const chatBoxRef = useRef(null);
  const inputBoxRef = useRef(null); // ref to manage hover-close behavior
  const composerInputRef = useRef(null);
  const bottomSentinelRef = useRef(null);
  const messageRefs = useRef({});
  const roomRef = useRef(null);
  const peerConnectionRef = useRef(null);
  const voiceChannelRef = useRef(null);
  const voiceStreamRef = useRef(null);
  const liveSessionAttemptRef = useRef(0);
  const remoteAudioRef = useRef(null);
  const liveStreamStateRef = useRef({ sessionId: null, currentTurn: null });
  const [recording, setRecording] = useState(false);
  const [liveSessionPending, setLiveSessionPending] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [liveStreamingPhase, setLiveStreamingPhase] = useState("idle");
  const [liveStreamingTranscript, setLiveStreamingTranscript] = useState({
    user: "",
    assistant: "",
  });
  const [audioRecording, setAudioRecording] = useState(false);
  const [ttsActiveMessageId, setTtsActiveMessageId] = useState(null);
  const [ttsPlayback, setTtsPlayback] = useState({
    messageId: null,
    status: "idle",
    currentTime: 0,
    duration: 0,
  });
  const ttsAudioRef = useRef(null);
  const [collapsedTools, setCollapsedTools] = useState({});
  const [collapseAllTools, setCollapseAllTools] = useState(true);
  const thinkingMode = state.thinkingMode || "auto";
  const workflowProfile = state.workflowProfile || "default";
  const preferredMicDeviceId = String(state.preferredMicDeviceId || "");
  const preferredCameraDeviceId = String(state.preferredCameraDeviceId || "");
  const micInputGain = clamp(Number(state.micInputGain) || 1, 0.25, 2);
  const outputVolume = clamp(Number(state.outputVolume) || 1, 0, 1.5);
  const liveCameraDefaultEnabled = state.liveCameraDefaultEnabled === true;
  const thinkingPayload =
    thinkingMode === "auto" ? {} : { thinking: thinkingMode };
  const workflowPayload = {
    workflow: workflowProfile,
    modules: Array.isArray(state.enabledWorkflowModules)
      ? state.enabledWorkflowModules
      : [],
  };
  const setThinkingMode = useCallback((mode) => {
    const normalized =
      mode === "high" || mode === "low" || mode === "auto" ? mode : "auto";
    setState((prev) => {
      if ((prev.thinkingMode || "auto") === normalized) return prev;
      return { ...prev, thinkingMode: normalized };
    });
  }, [setState]);
  const setWorkflowProfile = useCallback((workflow) => {
    const normalized = ["default", "architect_planner", "mini_execution"].includes(
      workflow,
    )
      ? workflow
      : "default";
    setState((prev) => {
      if ((prev.workflowProfile || "default") === normalized) return prev;
      return { ...prev, workflowProfile: normalized };
    });
  }, [setState]);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const activeRequestRef = useRef(null);
  const [toolEditorState, setToolEditorState] = useState(null); // { tool, onSubmit }
  const [messageEditorState, setMessageEditorState] = useState(null); // { mode: "user"|"assistant", assistantId, text }
  const [browserSessionPopup, setBrowserSessionPopup] = useState(null);
  const [browserPopupPendingAction, setBrowserPopupPendingAction] = useState("");
  const [browserPopupError, setBrowserPopupError] = useState("");
  const [browserNavigateDraft, setBrowserNavigateDraft] = useState("");
  const [browserTypeDraft, setBrowserTypeDraft] = useState("");
  const [browserKeyDraft, setBrowserKeyDraft] = useState("Enter");
  const [browserSessionOverrides, setBrowserSessionOverrides] = useState({});
  const [hoverChainId, setHoverChainId] = useState(null);
  const [activeChainId, setActiveChainId] = useState(null);
  const [entryOpen, setEntryOpen] = useState(true);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [composerRows, setComposerRows] = useState(DEFAULT_COMPOSER_ROWS);
  const initialScrollRef = useRef(false);
  const toolContinueLocksRef = useRef(new Set());
  const entryHoverTimer = useRef(null); // hover timer for open/close
  const inputOffsetRef = useRef(null);
  const inputOffsetRafRef = useRef(null);
  const inputOffsetTimerRef = useRef(null);
  const highlightChainId = hoverChainId || activeChainId;
  const toolDisplayMode = useMemo(
    () => normalizeToolDisplayMode(state.toolDisplayMode),
    [state.toolDisplayMode],
  );
  const toolLinkBehavior = useMemo(() => {
    const raw = state.toolLinkBehavior || "console";
    const normalized = String(raw).trim().toLowerCase();
    if (normalized === "inline" || normalized === "console") {
      return normalized;
    }
    return "console";
  }, [state.toolLinkBehavior]);
  const inlineToolsEnabled = toolDisplayMode !== "console";
  const shouldShowInlineToolsForMessage = useCallback(
    (msg, idx) => {
      if (!inlineToolsEnabled) return false;
      if (toolDisplayMode === "inline" || toolDisplayMode === "both") return true;
      if (!msg?.id) return false;
      if (activeMessageId && msg.id === activeMessageId) return true;
      if (highlightChainId && msg.id === highlightChainId) return true;
      return (
        toolDisplayMode === "auto" &&
        isStreaming &&
        msg.role === "ai" &&
        idx === state.conversation.length - 1
      );
    },
    [
      activeMessageId,
      highlightChainId,
      inlineToolsEnabled,
      isStreaming,
      state.conversation.length,
      toolDisplayMode,
    ],
  );
  const activeModeModel = useMemo(
    () => resolveModeModel(state.backendMode, state),
    [state.backendMode, state.apiModel, state.localModel, state.transformerModel],
  );
  const activeModelLabel = useMemo(
    () => formatModelSourceLabel(activeModeModel.mode, activeModeModel.model),
    [activeModeModel.mode, activeModeModel.model],
  );
  const toolChainIds = useMemo(() => {
    const ids = new Set();
    thoughts.forEach((t) => {
      if (t?.type === "tool" && t.chain_id) ids.add(t.chain_id);
    });
    return ids;
  }, [thoughts]);
  const hasAnyTools = useMemo(() => {
    if (!inlineToolsEnabled) return false;
    if (!Array.isArray(state.conversation) || state.conversation.length === 0) {
      return false;
    }
    return state.conversation.some(
      (msg, idx) =>
        shouldShowInlineToolsForMessage(msg, idx) &&
        resolveMessageTools(msg).length > 0,
    );
  }, [inlineToolsEnabled, shouldShowInlineToolsForMessage, state.conversation]);
  const browserSessionContexts = useMemo(() => {
    const sessions = new Map();
    let order = 0;
    (Array.isArray(state.conversation) ? state.conversation : []).forEach((msg) => {
      resolveMessageTools(msg).forEach((tool) => {
        const context = getBrowserSessionConversationContext(msg, tool, order);
        order += 1;
        if (!context?.sessionId) return;
        const override = browserSessionOverrides[context.sessionId];
        const merged = override ? { ...context, ...override } : context;
        const existing = sessions.get(context.sessionId);
        if (!existing || merged.order >= existing.order) {
          sessions.set(context.sessionId, merged);
        }
      });
    });
    Object.entries(browserSessionOverrides).forEach(([sessionId, context]) => {
      if (!sessionId || !context) return;
      const existing = sessions.get(sessionId);
      if (!existing || (context.order ?? Number.MAX_SAFE_INTEGER) >= existing.order) {
        sessions.set(sessionId, { ...existing, ...context, sessionId });
      }
    });
    return sessions;
  }, [browserSessionOverrides, state.conversation]);
  const activeBrowserSession = useMemo(() => {
    const sessionId =
      browserSessionPopup && typeof browserSessionPopup.sessionId === "string"
        ? browserSessionPopup.sessionId
        : "";
    return sessionId ? browserSessionContexts.get(sessionId) || null : null;
  }, [browserSessionContexts, browserSessionPopup]);
  const baseTimeoutSec = useMemo(() => {
    const fromState = Number(state.requestTimeoutSec);
    if (Number.isFinite(fromState) && fromState > 0) {
      return fromState;
    }
    return 30;
  }, [state.requestTimeoutSec]);
  const idleTimeoutSec = useMemo(() => {
    const fromState = Number(state.streamIdleTimeoutSec);
    if (Number.isFinite(fromState) && fromState > 0) {
      return fromState;
    }
    return 120;
  }, [state.streamIdleTimeoutSec]);
  const applySessionDisplayName = useCallback(
    (displayName) => {
      if (typeof displayName !== "string" || !displayName.trim()) return;
      setState((prev) => {
        if (prev.sessionName === displayName) {
          return prev;
        }
        return { ...prev, sessionName: displayName };
      });
    },
    [setState],
  );

  const getMessageSourceLabel = useCallback(
    (msg) => {
      if (!msg || typeof msg !== "object") return "";
      const meta = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
      const mode = typeof meta.mode === "string" ? meta.mode : "";
      const model = typeof meta.model === "string" ? meta.model : "";
      return formatModelSourceLabel(mode, model);
    },
    [],
  );

  const persistHistorySnapshot = useCallback((sessionId, history) => {
    try {
      localStorage.setItem("history", JSON.stringify(history));
      const payload = JSON.stringify({
        sessionId,
        history,
      });
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon("/api/history", blob);
      } else {
        axios
          .post("/api/history", {
            sessionId,
            history,
          })
          .catch(() => {});
      }
    } catch {}
  }, []);

  const buildHistoryFromConversation = useCallback((conversation) => {
    if (!Array.isArray(conversation)) return [];
    return conversation
      .filter(
        (entry) =>
          entry &&
          (entry.role === "user" || entry.role === "ai" || entry.role === "assistant") &&
          typeof entry.text === "string" &&
          entry.text.trim(),
      )
      .map((entry) => ({
        role: entry.role === "assistant" ? "ai" : entry.role,
        text: entry.text,
      }));
  }, []);

  const syncHistoryFromConversation = useCallback(
    (sessionId, conversation) => {
      const history = buildHistoryFromConversation(conversation);
      persistHistorySnapshot(sessionId, history);
      return history;
    },
    [buildHistoryFromConversation, persistHistorySnapshot],
  );

  const finalizeCurrentLiveTurn = useCallback(
    ({ partial = false, clearTranscript = false } = {}) => {
      const liveState = liveStreamStateRef.current;
      const turn = liveState.currentTurn;
      if (!turn) {
        if (clearTranscript) {
          setLiveStreamingTranscript({ user: "", assistant: "" });
        }
        return;
      }
      const assistantText = String(turn.assistantText || "").trim();
      if (assistantText && !turn.assistantCommitted) {
        setState((prev) => {
          const updatedConversation = Array.isArray(prev.conversation)
            ? [...prev.conversation]
            : [];
          const timestampIso = new Date().toISOString();
          const existingIdx = updatedConversation.findIndex(
            (entry) => entry && entry.id === turn.assistantMessageId,
          );
          const assistantEntry = {
            role: "ai",
            id: turn.assistantMessageId,
            text: assistantText,
            thoughts: [],
            tools: Array.isArray(turn.tools) ? turn.tools : [],
            timestamp: timestampIso,
            metadata: {
              status: partial ? "streaming_stopped" : "completed",
              live_stream: {
                source: "realtime",
                session_id: liveState.sessionId,
                partial,
              },
            },
          };
          if (existingIdx === -1) {
            updatedConversation.push(assistantEntry);
          } else {
            updatedConversation[existingIdx] = {
              ...updatedConversation[existingIdx],
              ...assistantEntry,
              metadata: {
                ...(updatedConversation[existingIdx]?.metadata || {}),
                ...(assistantEntry.metadata || {}),
              },
            };
          }
          const history = syncHistoryFromConversation(
            prev.sessionId,
            updatedConversation,
          );
          return {
            ...prev,
            conversation: updatedConversation,
            history,
          };
        });
        turn.assistantCommitted = true;
      }
      liveState.currentTurn = null;
      if (clearTranscript) {
        setLiveStreamingTranscript({ user: "", assistant: "" });
      }
    },
    [setState, syncHistoryFromConversation],
  );

  const upsertLiveUserConversationEntry = useCallback(
    ({ text = "" } = {}) => {
      const liveState = liveStreamStateRef.current;
      const turn = liveState.currentTurn;
      const userText = String(text || "").trim();
      if (!turn?.userMessageId || !userText) return;
      setState((prev) => {
        const updatedConversation = Array.isArray(prev.conversation)
          ? [...prev.conversation]
          : [];
        const existingIdx = updatedConversation.findIndex(
          (entry) => entry && entry.id === turn.userMessageId,
        );
        const userEntry = {
          id: turn.userMessageId,
          role: "user",
          text: userText,
          timestamp:
            updatedConversation[existingIdx]?.timestamp || new Date().toISOString(),
          metadata: {
            live_stream: {
              source: "realtime",
              session_id: liveState.sessionId,
            },
          },
        };
        if (existingIdx === -1) {
          updatedConversation.push(userEntry);
        } else {
          updatedConversation[existingIdx] = {
            ...updatedConversation[existingIdx],
            ...userEntry,
            metadata: {
              ...(updatedConversation[existingIdx]?.metadata || {}),
              ...(userEntry.metadata || {}),
            },
          };
        }
        const history = syncHistoryFromConversation(
          prev.sessionId,
          updatedConversation,
        );
        return {
          ...prev,
          conversation: updatedConversation,
          history,
        };
      });
    },
    [setState, syncHistoryFromConversation],
  );

  const commitLiveUserTranscript = useCallback(
    (transcript, { itemId = null } = {}) => {
      const normalized = String(transcript || "")
        .replace(/\s+/g, " ")
        .trim();
      if (!normalized) return;
      const liveState = liveStreamStateRef.current;
      if (liveState.currentTurn) {
        const turn = liveState.currentTurn;
        if (itemId) {
          turn.userItemId = String(itemId);
        }
        if (
          !turn.userText ||
          turn.userText === normalized ||
          !String(turn.assistantText || "").trim()
        ) {
          turn.userText = normalized;
          upsertLiveUserConversationEntry({ text: normalized });
          setLiveStreamingTranscript((prev) => ({
            user: normalized,
            assistant: turn.assistantText || prev.assistant || "",
          }));
          return;
        }
        setLiveStreamingTranscript((prev) => ({
          user: normalized,
          assistant: turn.assistantText || prev.assistant || "",
        }));
        finalizeCurrentLiveTurn({
          partial: Boolean(String(turn.assistantText || "").trim()),
        });
      }
      const turnId = createClientMessageId("live");
      liveState.currentTurn = {
        turnId,
        userMessageId: `${turnId}:user`,
        assistantMessageId: turnId,
        userItemId: itemId ? String(itemId) : null,
        userText: normalized,
        assistantText: "",
        tools: [],
        assistantCommitted: false,
      };
      setLiveStreamingTranscript({
        user: normalized,
        assistant: "",
      });
      upsertLiveUserConversationEntry({ text: normalized });
    },
    [finalizeCurrentLiveTurn, upsertLiveUserConversationEntry],
  );

  const updateLiveAssistantTranscript = useCallback((text, { replace = false } = {}) => {
    const chunk = typeof text === "string" ? text : "";
    if (!chunk && !replace) return;
    const liveState = liveStreamStateRef.current;
    if (!liveState.currentTurn) {
      const turnId = createClientMessageId("live");
      liveState.currentTurn = {
        turnId,
        userMessageId: `${turnId}:user`,
        assistantMessageId: turnId,
        userItemId: null,
        userText: "",
        assistantText: "",
        tools: [],
        assistantCommitted: false,
      };
    }
    const turn = liveState.currentTurn;
    turn.assistantText = replace ? chunk : `${turn.assistantText || ""}${chunk}`;
    setLiveStreamingTranscript((prev) => ({
      user: turn.userText || prev.user,
      assistant: turn.assistantText,
    }));
  }, []);

  const upsertLiveAssistantConversationEntry = useCallback(
    ({ text = "", tools = [], status = "streaming" } = {}) => {
      const liveState = liveStreamStateRef.current;
      const turn = liveState.currentTurn;
      if (!turn?.assistantMessageId) return;
      setState((prev) => {
        const updatedConversation = Array.isArray(prev.conversation)
          ? [...prev.conversation]
          : [];
        const timestampIso = new Date().toISOString();
        const existingIdx = updatedConversation.findIndex(
          (entry) => entry && entry.id === turn.assistantMessageId,
        );
        const existingEntry =
          existingIdx >= 0 && updatedConversation[existingIdx]
            ? updatedConversation[existingIdx]
            : null;
        const mergedTools = mergeToolEntries(
          existingEntry?.tools,
          tools,
          existingEntry?.metadata,
        );
        const assistantEntry = {
          ...(existingEntry || {}),
          id: turn.assistantMessageId,
          role: "ai",
          text:
            typeof text === "string" && text.trim()
              ? text
              : existingEntry?.text || turn.assistantText || "",
          thoughts: Array.isArray(existingEntry?.thoughts)
            ? existingEntry.thoughts
            : [],
          tools: mergedTools,
          timestamp: existingEntry?.timestamp || timestampIso,
          metadata: {
            ...(existingEntry?.metadata || {}),
            status,
            live_stream: {
              source: "realtime",
              session_id: liveState.sessionId,
              partial: status !== "completed",
            },
          },
        };
        if (existingIdx === -1) {
          updatedConversation.push(assistantEntry);
        } else {
          updatedConversation[existingIdx] = assistantEntry;
        }
        const history = syncHistoryFromConversation(
          prev.sessionId,
          updatedConversation,
        );
        return {
          ...prev,
          conversation: updatedConversation,
          history,
        };
      });
    },
    [setState, syncHistoryFromConversation],
  );

  const noteLiveToolResult = useCallback(
    (toolEntry, { status = "streaming" } = {}) => {
      const liveState = liveStreamStateRef.current;
      if (!liveState.currentTurn) {
        const turnId = createClientMessageId("live");
        liveState.currentTurn = {
          turnId,
          userMessageId: `${turnId}:user`,
          assistantMessageId: turnId,
          userItemId: null,
          userText: liveStreamingTranscript.user || "",
          assistantText: liveStreamingTranscript.assistant || "",
          tools: [],
          assistantCommitted: false,
        };
      }
      const turn = liveState.currentTurn;
      turn.tools = mergeToolEntries(turn.tools, [toolEntry]);
      upsertLiveAssistantConversationEntry({
        text: turn.assistantText || "",
        tools: turn.tools,
        status,
      });
    },
    [liveStreamingTranscript.assistant, liveStreamingTranscript.user, upsertLiveAssistantConversationEntry],
  );

  const stopMediaStream = useCallback((stream) => {
    if (!stream || typeof stream.getTracks !== "function") return;
    try {
      stream.getTracks().forEach((track) => track.stop());
    } catch (_) {}
  }, []);

  const refreshAvailableInputDevices = useCallback(async () => {
    if (!navigator?.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const counters = {
        audioinput: 0,
        videoinput: 0,
      };
      const next = {
        audioinput: [],
        videoinput: [],
      };
      devices.forEach((device) => {
        if (device.kind !== "audioinput" && device.kind !== "videoinput") return;
        counters[device.kind] += 1;
        const fallbackLabel =
          device.kind === "audioinput"
            ? `Microphone ${counters[device.kind]}`
            : `Camera ${counters[device.kind]}`;
        next[device.kind].push({
          deviceId: device.deviceId,
          label: device.label || fallbackLabel,
        });
      });
      setAvailableInputDevices(next);
    } catch (err) {
      console.error("device enumeration failed", err);
    }
  }, []);

  const clearVoiceAudioPipeline = useCallback(() => {
    stopMediaStream(voiceStreamRef.current);
    stopMediaStream(voiceSourceStreamRef.current);
    voiceStreamRef.current = null;
    voiceSourceStreamRef.current = null;
    voiceGainNodeRef.current = null;
    const audioContext = voiceAudioContextRef.current;
    voiceAudioContextRef.current = null;
    if (audioContext && typeof audioContext.close === "function") {
      audioContext.close().catch(() => {});
    }
  }, [stopMediaStream]);

  const buildAudioConstraints = useCallback(() => {
    const audio = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (preferredMicDeviceId) {
      audio.deviceId = { exact: preferredMicDeviceId };
    }
    return audio;
  }, [preferredMicDeviceId]);

  const buildCameraConstraints = useCallback(() => {
    const video = {
      facingMode: "environment",
    };
    if (preferredCameraDeviceId) {
      video.deviceId = { exact: preferredCameraDeviceId };
    }
    return video;
  }, [preferredCameraDeviceId]);

  const createProcessedAudioInput = useCallback(async () => {
    if (!navigator?.mediaDevices?.getUserMedia) {
      throw new Error("Microphone access is not available in this browser.");
    }
    clearVoiceAudioPipeline();
    const rawStream = await navigator.mediaDevices.getUserMedia({
      audio: buildAudioConstraints(),
    });
    voiceSourceStreamRef.current = rawStream;
    const AudioContextCtor =
      typeof window !== "undefined"
        ? window.AudioContext || window.webkitAudioContext
        : null;
    if (typeof AudioContextCtor !== "function") {
      voiceStreamRef.current = rawStream;
      return { stream: rawStream };
    }
    const audioContext = new AudioContextCtor();
    voiceAudioContextRef.current = audioContext;
    if (audioContext.state === "suspended") {
      try {
        await audioContext.resume();
      } catch (_) {}
    }
    const source = audioContext.createMediaStreamSource(rawStream);
    const gainNode = audioContext.createGain();
    gainNode.gain.value = micInputGain;
    voiceGainNodeRef.current = gainNode;
    const destination = audioContext.createMediaStreamDestination();
    source.connect(gainNode);
    gainNode.connect(destination);
    const processedStream = destination.stream;
    voiceStreamRef.current = processedStream;
    return { stream: processedStream };
  }, [buildAudioConstraints, clearVoiceAudioPipeline, micInputGain]);

  const stopMicTest = useCallback(() => {
    const micTest = micTestRef.current;
    if (micTest.rafId) {
      cancelAnimationFrame(micTest.rafId);
    }
    micTest.rafId = null;
    stopMediaStream(micTest.processedStream);
    stopMediaStream(micTest.rawStream);
    micTest.processedStream = null;
    micTest.rawStream = null;
    micTest.analyser = null;
    if (micTest.audioContext && typeof micTest.audioContext.close === "function") {
      micTest.audioContext.close().catch(() => {});
    }
    micTest.audioContext = null;
    setMicTestActive(false);
    setMicTestLevel(0);
  }, [stopMediaStream]);

  const updateChatSettingsPopoverPosition = useCallback(() => {
    if (typeof window === "undefined") return;
    const trigger = chatSettingsTriggerRef.current;
    if (!trigger) return;
    const triggerRect = trigger.getBoundingClientRect();
    const popoverRect = chatSettingsPopoverRef.current?.getBoundingClientRect();
    const width = popoverRect?.width || 388;
    const height = popoverRect?.height || 312;
    const margin = 12;
    const gap = 10;
    const viewportWidth =
      window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight =
      window.innerHeight || document.documentElement.clientHeight || 0;
    const maxLeft = Math.max(margin, viewportWidth - width - margin);
    const left = clamp(triggerRect.right - width, margin, maxLeft);
    const aboveTop = triggerRect.top - height - gap;
    const belowTop = triggerRect.bottom + gap;
    const top =
      aboveTop >= margin
        ? aboveTop
        : clamp(
            belowTop,
            margin,
            Math.max(margin, viewportHeight - height - margin),
          );
    setChatSettingsPopoverStyle({
      position: "fixed",
      top: `${Math.round(top)}px`,
      left: `${Math.round(left)}px`,
      maxWidth: `min(calc(100vw - ${margin * 2}px), 388px)`,
      zIndex: 1400,
    });
  }, []);

  const startMicTest = useCallback(async () => {
    if (!navigator?.mediaDevices?.getUserMedia) {
      setBanner({
        message: "Mic test unavailable",
        hint: "This browser does not support microphone access.",
        category: "warning",
      });
      return;
    }
    stopMicTest();
    try {
      const rawStream = await navigator.mediaDevices.getUserMedia({
        audio: buildAudioConstraints(),
      });
      const AudioContextCtor =
        typeof window !== "undefined"
          ? window.AudioContext || window.webkitAudioContext
          : null;
      if (typeof AudioContextCtor !== "function") {
        micTestRef.current.rawStream = rawStream;
        micTestRef.current.processedStream = rawStream;
        setMicTestActive(true);
        setMicTestLevel(0.6);
        return;
      }
      const audioContext = new AudioContextCtor();
      if (audioContext.state === "suspended") {
        try {
          await audioContext.resume();
        } catch (_) {}
      }
      const source = audioContext.createMediaStreamSource(rawStream);
      const gainNode = audioContext.createGain();
      gainNode.gain.value = micInputGain;
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(gainNode);
      gainNode.connect(analyser);
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      micTestRef.current = {
        rawStream,
        processedStream: rawStream,
        audioContext,
        analyser,
        rafId: null,
      };
      const tick = () => {
        const current = micTestRef.current;
        if (!current?.analyser) return;
        current.analyser.getByteTimeDomainData(buffer);
        let peak = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          peak = Math.max(peak, Math.abs(buffer[i] - 128) / 128);
        }
        setMicTestLevel(clamp(peak * 1.8, 0, 1));
        current.rafId = requestAnimationFrame(tick);
      };
      setMicTestActive(true);
      tick();
    } catch (err) {
      console.error("mic test failed", err);
      setBanner({
        message: "Mic test failed",
        hint: "Microphone access was denied or unavailable.",
        category: "warning",
      });
    }
  }, [buildAudioConstraints, micInputGain, stopMicTest]);

  const toggleMicTest = useCallback(async () => {
    if (micTestActive) {
      stopMicTest();
      return;
    }
    await startMicTest();
  }, [micTestActive, startMicTest, stopMicTest]);

  const captureStillFrameFromStream = useCallback(async (stream, filenameBase) => {
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await new Promise((resolve, reject) => {
      const cleanup = () => {
        video.onloadedmetadata = null;
        video.onerror = null;
      };
      video.onloadedmetadata = () => {
        cleanup();
        resolve();
      };
      video.onerror = () => {
        cleanup();
        reject(new Error("Video metadata unavailable."));
      };
    });
    const playAttempt = video.play();
    if (playAttempt && typeof playAttempt.catch === "function") {
      await playAttempt.catch(() => {});
    }
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("Could not access capture buffer.");
    }
    ctx.drawImage(video, 0, 0, width, height);
    const blob = await new Promise((resolve) => {
      canvas.toBlob(resolve, "image/png");
    });
    if (!(blob instanceof Blob)) {
      throw new Error("Capture failed.");
    }
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    return new File([blob], `${filenameBase}-${stamp}.png`, {
      type: "image/png",
    });
  }, []);

  const releaseLiveVisualTrack = useCallback(async () => {
    const currentStream = liveVisualStreamRef.current;
    liveVisualStreamRef.current = null;
    liveVisualTrackRef.current = null;
    if (liveVisualPreviewRef.current) {
      try {
        liveVisualPreviewRef.current.srcObject = null;
      } catch (_) {}
    }
    if (roomRef.current && liveVisualPublicationRef.current) {
      try {
        await roomRef.current.localParticipant.unpublishTrack(
          liveVisualPublicationRef.current.track,
          true,
        );
      } catch (_) {}
      liveVisualPublicationRef.current = null;
    }
    if (liveVisualSenderRef.current) {
      try {
        await liveVisualSenderRef.current.replaceTrack(null);
      } catch (_) {}
    }
    stopMediaStream(currentStream);
  }, [stopMediaStream]);

  const attachLiveVisualTrack = useCallback(
    async (stream, mode) => {
      const track = stream?.getVideoTracks?.()[0] || null;
      if (!track) {
        throw new Error("No video track was available.");
      }
      await releaseLiveVisualTrack();
      liveVisualStreamRef.current = stream;
      liveVisualTrackRef.current = track;
      if (liveVisualPreviewRef.current) {
        try {
          liveVisualPreviewRef.current.srcObject = stream;
          const playAttempt = liveVisualPreviewRef.current.play();
          if (playAttempt && typeof playAttempt.catch === "function") {
            playAttempt.catch(() => {});
          }
        } catch (_) {}
      }
      if (peerConnectionRef.current && liveVisualSenderRef.current) {
        await liveVisualSenderRef.current.replaceTrack(track);
      } else if (roomRef.current) {
        liveVisualPublicationRef.current =
          await roomRef.current.localParticipant.publishTrack(track);
      }
      setLiveVisualMode(mode);
      setLiveVisualError("");
    },
    [releaseLiveVisualTrack],
  );

  const enableLiveCamera = useCallback(async () => {
    if (!navigator?.mediaDevices?.getUserMedia) {
      throw new Error("Camera access is not available in this browser.");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      video: buildCameraConstraints(),
      audio: false,
    });
    await attachLiveVisualTrack(stream, "camera");
  }, [attachLiveVisualTrack, buildCameraConstraints]);

  const enableLiveScreenShare = useCallback(async () => {
    if (!navigator?.mediaDevices?.getDisplayMedia) {
      throw new Error("Desktop capture is not available in this browser.");
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        cursor: "always",
      },
      audio: false,
    });
    const [track] = stream.getVideoTracks();
    if (track) {
      track.addEventListener(
        "ended",
        () => {
          releaseLiveVisualTrack()
            .then(() => setLiveVisualMode("off"))
            .catch(() => {});
        },
        { once: true },
      );
    }
    await attachLiveVisualTrack(stream, "screen");
  }, [attachLiveVisualTrack, releaseLiveVisualTrack]);

  const sendRealtimeClientEvent = useCallback((event) => {
    const channel = voiceChannelRef.current;
    if (!channel || channel.readyState !== "open") return false;
    try {
      channel.send(JSON.stringify(event));
      return true;
    } catch (err) {
      console.error("realtime client event failed", err);
      return false;
    }
  }, []);

  const ensureLiveSessionAttemptCurrent = useCallback((attemptId) => {
    if (attemptId !== liveSessionAttemptRef.current) {
      throw createLiveSessionCancelledError();
    }
  }, []);

  const requestRealtimeAssistantResponse = useCallback(
    ({ force = false } = {}) => {
      const lifecycle = realtimeResponseLifecycleRef.current;
      if (!force && (lifecycle.active || lifecycle.requested)) {
        return false;
      }
      const sent = sendRealtimeClientEvent({ type: "response.create" });
      if (sent) {
        lifecycle.requested = true;
      }
      return sent;
    },
    [sendRealtimeClientEvent],
  );

  const configureRealtimeTools = useCallback(async () => {
    if (realtimeConfiguredToolsRef.current) return;
    const channel = voiceChannelRef.current;
    if (!channel || channel.readyState !== "open") return;
    try {
      const res = await axios.get("/api/tools/specs");
      const tools = Array.isArray(res?.data?.tools) ? res.data.tools : [];
      const safeTools = tools
        .filter((tool) => SAFE_REALTIME_TOOL_NAMES.includes(tool?.name))
        .map((tool) => ({
          type: "function",
          name: tool.name,
          description: tool.description,
          parameters:
            tool.parameters && typeof tool.parameters === "object"
              ? normalizeRealtimeToolSchema(tool.parameters)
              : { type: "object", properties: {} },
        }));
      const turnDetection = realtimeTurnDetectionRef.current || {};
      const sessionUpdate = {
        type: "realtime",
        audio: {
          input: {
            turn_detection: {
              type: turnDetection.type || "server_vad",
              create_response: false,
              interrupt_response: turnDetection.interrupt_response !== false,
            },
          },
        },
      };
      if (safeTools.length) {
        sessionUpdate.tool_choice = "auto";
        sessionUpdate.tools = safeTools;
      }
      const sent = sendRealtimeClientEvent({
        type: "session.update",
        session: sessionUpdate,
      });
      if (sent) {
        realtimeConfiguredToolsRef.current = true;
      }
    } catch (err) {
      console.error("realtime tool setup failed", err);
    }
  }, [sendRealtimeClientEvent]);

  const invokeRealtimeToolCall = useCallback(
    async ({ callId, name, args }) => {
      const normalizedCallId = String(callId || "").trim();
      if (!normalizedCallId) return;
      const liveState = liveStreamStateRef.current;
      if (!liveState.currentTurn) {
        const turnId = createClientMessageId("live");
        liveState.currentTurn = {
          turnId,
          userMessageId: `${turnId}:user`,
          assistantMessageId: turnId,
          userItemId: null,
          userText: liveStreamingTranscript.user || "",
          assistantText: liveStreamingTranscript.assistant || "",
          tools: [],
          assistantCommitted: false,
        };
      }
      const assistantMessageId = liveState.currentTurn.assistantMessageId;
      const toolEntryBase = {
        id: normalizedCallId,
        request_id: normalizedCallId,
        name,
        args,
        status: "invoking",
        session_id: state.sessionId,
      };
      noteLiveToolResult(toolEntryBase, { status: "streaming" });
      try {
        const resp = await axios.post("/api/tools/invoke", {
          name,
          args,
          session_id: state.sessionId,
          message_id: assistantMessageId,
          chain_id: assistantMessageId,
        });
        const result = resp?.data?.result;
        noteLiveToolResult(
          {
            ...toolEntryBase,
            result,
            status: "invoked",
          },
          { status: "streaming" },
        );
        sendRealtimeClientEvent({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id: normalizedCallId,
            output: JSON.stringify(result ?? { ok: true }),
          },
        });
        realtimePendingToolCallsRef.current.delete(normalizedCallId);
        if (!realtimePendingToolCallsRef.current.size) {
          requestRealtimeAssistantResponse();
        }
      } catch (err) {
        console.error("realtime tool invoke failed", err);
        const detail = getRequestErrorDetail(err, "Tool invoke failed.");
        const errorResult = buildToolOutcomeResult("error", detail);
        noteLiveToolResult(
          {
            ...toolEntryBase,
            result: errorResult,
            status: "error",
          },
          { status: "streaming" },
        );
        sendRealtimeClientEvent({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id: normalizedCallId,
            output: JSON.stringify(errorResult),
          },
        });
        realtimePendingToolCallsRef.current.delete(normalizedCallId);
        if (!realtimePendingToolCallsRef.current.size) {
          requestRealtimeAssistantResponse();
        }
      }
    },
    [
      liveStreamingTranscript.assistant,
      liveStreamingTranscript.user,
      noteLiveToolResult,
      requestRealtimeAssistantResponse,
      sendRealtimeClientEvent,
      state.sessionId,
    ],
  );

  const handleRealtimeFunctionCall = useCallback(
    async ({ callId, name, argumentsText }) => {
      const normalizedCallId = String(callId || "").trim();
      const normalizedName = String(name || "").trim();
      if (!normalizedCallId || !normalizedName) return;
      const current = realtimeToolStateRef.current[normalizedCallId] || {};
      if (current.handled) return;
      let args = {};
      if (typeof argumentsText === "string" && argumentsText.trim()) {
        try {
          args = JSON.parse(argumentsText);
        } catch (err) {
          console.error("realtime tool args parse failed", err);
          args = {};
        }
      }
      realtimeToolStateRef.current[normalizedCallId] = {
        callId: normalizedCallId,
        name: normalizedName,
        argumentsText: typeof argumentsText === "string" ? argumentsText : "",
        handled: true,
      };
      realtimePendingToolCallsRef.current.add(normalizedCallId);
      realtimeResponseLifecycleRef.current = {
        active: false,
        requested: false,
      };
      await invokeRealtimeToolCall({
        callId: normalizedCallId,
        name: normalizedName,
        args,
      });
    },
    [invokeRealtimeToolCall],
  );

  async function toggleLiveCamera() {
    if (!recording) {
      await openCameraCapture();
      return;
    }
    if (liveVisualMode === "camera") {
      await releaseLiveVisualTrack();
      setLiveVisualMode("off");
      return;
    }
    try {
      setLiveVisualError("");
      await enableLiveCamera();
    } catch (err) {
      console.error("live camera failed", err);
      setLiveVisualError(getRequestErrorDetail(err, "Camera access failed."));
    }
  }

  async function toggleLiveScreenShare() {
    if (!recording) {
      if (!navigator?.mediaDevices?.getDisplayMedia) {
        setError("Desktop capture is not available in this browser.");
        return;
      }
      setScreenCaptureBusy(true);
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({
          video: {
            cursor: "always",
          },
          audio: false,
        });
        const capturedFile = await captureStillFrameFromStream(stream, "desktop");
        await uploadAndAttach(capturedFile, {
          origin: "captured",
          captureSource: "desktop_capture",
        });
        stopMediaStream(stream);
      } catch (err) {
        console.error("desktop capture failed", err);
        setError(getRequestErrorDetail(err, "Desktop capture failed."));
      } finally {
        stopMediaStream(stream);
        setScreenCaptureBusy(false);
      }
      return;
    }
    if (liveVisualMode === "screen") {
      await releaseLiveVisualTrack();
      setLiveVisualMode("off");
      return;
    }
    try {
      setLiveVisualError("");
      await enableLiveScreenShare();
    } catch (err) {
      console.error("live screen share failed", err);
      setLiveVisualError(getRequestErrorDetail(err, "Desktop capture failed."));
    }
  }

  const handleAttachmentFileAction = useCallback(() => {
    setAttachmentMenuOpen(false);
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  }, []);

  const handleAttachmentCameraAction = useCallback(async () => {
    setAttachmentMenuOpen(false);
    await toggleLiveCamera();
  }, [toggleLiveCamera]);

  const handleAttachmentScreenAction = useCallback(async () => {
    setAttachmentMenuOpen(false);
    await toggleLiveScreenShare();
  }, [toggleLiveScreenShare]);

  const stopTtsPlayback = useCallback(() => {
    const audio = ttsAudioRef.current;
    if (audio) {
      try {
        audio.pause();
        audio.src = "";
      } catch (_) {}
    }
    ttsAudioRef.current = null;
    setTtsActiveMessageId(null);
    setTtsPlayback({
      messageId: null,
      status: "idle",
      currentTime: 0,
      duration: 0,
    });
  }, []);

  const stopLiveVoiceSession = useCallback(() => {
    finalizeCurrentLiveTurn({ partial: true, clearTranscript: true });
    liveSessionAttemptRef.current += 1;
    sendRealtimeClientEvent({ type: "response.cancel" });
    sendRealtimeClientEvent({ type: "input_audio_buffer.clear" });
    if (voiceChannelRef.current) {
      try {
        voiceChannelRef.current.close();
      } catch (_) {}
      voiceChannelRef.current = null;
    }
    if (roomRef.current) {
      try {
        roomRef.current.disconnect();
      } catch (_) {}
      roomRef.current = null;
    }
    if (peerConnectionRef.current) {
      try {
        peerConnectionRef.current.close();
      } catch (_) {}
      peerConnectionRef.current = null;
    }
    clearVoiceAudioPipeline();
    releaseLiveVisualTrack().catch(() => {});
    liveVisualSenderRef.current = null;
    liveVisualPublicationRef.current = null;
    if (remoteAudioRef.current) {
      try {
        remoteAudioRef.current.pause();
        remoteAudioRef.current.srcObject = null;
        if (typeof remoteAudioRef.current.remove === "function") {
          remoteAudioRef.current.remove();
        }
      } catch (_) {}
      remoteAudioRef.current = null;
    }
    liveStreamStateRef.current = { sessionId: null, currentTurn: null };
    realtimeToolStateRef.current = {};
    realtimePendingToolCallsRef.current = new Set();
    realtimeConfiguredToolsRef.current = false;
    realtimeResponseLifecycleRef.current = {
      active: false,
      requested: false,
    };
    setSpeaking(false);
    setRecording(false);
    setLiveSessionPending(false);
    setAttachmentMenuOpen(false);
    setLiveVisualMode("off");
    setLiveVisualError("");
    setLiveStreamingPhase("idle");
  }, [
    clearVoiceAudioPipeline,
    finalizeCurrentLiveTurn,
    releaseLiveVisualTrack,
    sendRealtimeClientEvent,
  ]);

  const handleRealtimeVoiceEvent = useCallback((rawEvent) => {
    let payload = rawEvent;
    if (typeof rawEvent === "string") {
      try {
        payload = JSON.parse(rawEvent);
      } catch {
        return;
      }
    }
    if (!payload || typeof payload !== "object") return;
    const type = typeof payload.type === "string" ? payload.type : "";
    if (type === "input_audio_buffer.speech_started") {
      realtimeResponseLifecycleRef.current = {
        active: false,
        requested: false,
      };
      setLiveStreamingPhase("user-speaking");
      if (!liveStreamStateRef.current.currentTurn) {
        setLiveStreamingTranscript({ user: "", assistant: "" });
      }
      return;
    }
    if (type === "input_audio_buffer.speech_stopped") {
      setLiveStreamingPhase("transcribing");
      return;
    }
    if (type === "input_audio_buffer.committed") {
      const itemId = String(payload.item_id || payload.item?.id || "").trim();
      if (!liveStreamStateRef.current.currentTurn) {
        const turnId = createClientMessageId("live");
        liveStreamStateRef.current.currentTurn = {
          turnId,
          userMessageId: `${turnId}:user`,
          assistantMessageId: turnId,
          userItemId: itemId || null,
          userText: "",
          assistantText: "",
          tools: [],
          assistantCommitted: false,
        };
      } else if (itemId) {
        liveStreamStateRef.current.currentTurn.userItemId = itemId;
      }
      return;
    }
    if (type === "output_audio_buffer.started") {
      setSpeaking(true);
      setLiveStreamingPhase("assistant-speaking");
      return;
    }
    if (type === "output_audio_buffer.stopped" || type === "response.audio.done") {
      setSpeaking(false);
      if (liveStreamStateRef.current.sessionId) {
        setLiveStreamingPhase("listening");
      }
      return;
    }
    if (type === "response.created") {
      realtimeResponseLifecycleRef.current = {
        active: true,
        requested: false,
      };
      setLiveStreamingPhase("assistant-thinking");
      return;
    }
    if (type === "conversation.item.created") {
      const item = payload.item;
      if (item?.type === "message" && item.role === "user") {
        const itemId = String(item.id || payload.item_id || "").trim();
        if (liveStreamStateRef.current.currentTurn && itemId) {
          liveStreamStateRef.current.currentTurn.userItemId = itemId;
        }
        const transcript = extractRealtimeTranscriptText({ item });
        if (transcript) {
          commitLiveUserTranscript(transcript, { itemId });
          requestRealtimeAssistantResponse();
        }
        return;
      }
      if (item?.type === "function_call") {
        const callId = item.call_id || item.id;
        if (callId) {
          realtimeToolStateRef.current[String(callId)] = {
            callId: String(callId),
            name: item.name || "",
            argumentsText: typeof item.arguments === "string" ? item.arguments : "",
            handled: false,
          };
        }
        return;
      }
    }
    if (LIVE_STREAM_INPUT_TRANSCRIPT_DELTA_TYPES.has(type)) {
      const transcript = extractRealtimeTranscriptText(payload);
      if (transcript) {
        setLiveStreamingTranscript((prev) => ({
          ...prev,
          user: transcript,
        }));
      }
      return;
    }
    if (LIVE_STREAM_INPUT_TRANSCRIPT_DONE_TYPES.has(type)) {
      const transcript = extractRealtimeTranscriptText(payload);
      const itemId = String(
        payload.item_id || payload.item?.id || payload.transcript_id || "",
      ).trim();
      if (transcript) {
        commitLiveUserTranscript(transcript, { itemId });
        requestRealtimeAssistantResponse();
      }
      setLiveStreamingPhase("assistant-thinking");
      return;
    }
    if (type === "response.function_call_arguments.delta") {
      const callId = String(payload.call_id || payload.item_id || "").trim();
      if (!callId) return;
      const current = realtimeToolStateRef.current[callId] || {
        callId,
        name: payload.name || "",
        argumentsText: "",
        handled: false,
      };
      current.argumentsText = `${current.argumentsText || ""}${
        typeof payload.delta === "string" ? payload.delta : ""
      }`;
      if (!current.name && payload.name) {
        current.name = payload.name;
      }
      realtimeToolStateRef.current[callId] = current;
      return;
    }
    if (type === "response.function_call_arguments.done") {
      const callId = String(payload.call_id || payload.item_id || "").trim();
      const current = realtimeToolStateRef.current[callId] || {};
      handleRealtimeFunctionCall({
        callId,
        name: payload.name || current.name,
        argumentsText:
          typeof payload.arguments === "string"
            ? payload.arguments
            : current.argumentsText || "",
      }).catch(() => {});
      return;
    }
    if (type === "response.output_item.done") {
      const item = payload.item;
      if (item?.type === "function_call") {
        handleRealtimeFunctionCall({
          callId: item.call_id || item.id,
          name: item.name,
          argumentsText:
            typeof item.arguments === "string" ? item.arguments : "",
        }).catch(() => {});
        return;
      }
      if (item?.type === "message" && item.role === "assistant") {
        if (!liveStreamStateRef.current.currentTurn) return;
        const transcript = extractRealtimeTranscriptText({ item });
        if (transcript) {
          updateLiveAssistantTranscript(transcript, { replace: true });
        }
        return;
      }
    }
    if (LIVE_STREAM_ASSISTANT_TRANSCRIPT_DELTA_TYPES.has(type)) {
      const transcript = extractRealtimeTranscriptText(payload);
      if (transcript) {
        updateLiveAssistantTranscript(transcript);
      }
      setLiveStreamingPhase("assistant-thinking");
      return;
    }
    if (LIVE_STREAM_ASSISTANT_TRANSCRIPT_DONE_TYPES.has(type)) {
      const transcript = extractRealtimeTranscriptText(payload);
      if (transcript) {
        updateLiveAssistantTranscript(transcript, { replace: true });
      }
      finalizeCurrentLiveTurn();
      if (liveStreamStateRef.current.sessionId) {
        setLiveStreamingPhase("listening");
      }
      return;
    }
    if (type === "response.done") {
      const outputs = Array.isArray(payload.response?.output)
        ? payload.response.output
        : [];
      const sawFunctionCall = outputs.some(
        (item) => item && typeof item === "object" && item.type === "function_call",
      );
      outputs.forEach((item) => {
        if (!item || typeof item !== "object") return;
        if (item.type === "function_call") {
          handleRealtimeFunctionCall({
            callId: item.call_id || item.id,
            name: item.name,
            argumentsText:
              typeof item.arguments === "string" ? item.arguments : "",
          }).catch(() => {});
          return;
        }
        if (item.type === "message" && item.role === "assistant") {
          if (!liveStreamStateRef.current.currentTurn) {
            return;
          }
          const transcript = extractRealtimeTranscriptText({ item });
          if (transcript) {
            updateLiveAssistantTranscript(transcript, { replace: true });
          }
        }
      });
      setSpeaking(false);
      realtimeResponseLifecycleRef.current = {
        active: false,
        requested: false,
      };
      if (sawFunctionCall) {
        setLiveStreamingPhase("assistant-thinking");
        return;
      }
      if (liveStreamStateRef.current.currentTurn) {
        finalizeCurrentLiveTurn();
      }
      if (liveStreamStateRef.current.sessionId) {
        setLiveStreamingPhase("listening");
      }
      return;
    }
    if (type === "error") {
      realtimeResponseLifecycleRef.current = {
        active: false,
        requested: false,
      };
      const message =
        payload.error?.message ||
        payload.message ||
        "OpenAI Realtime returned an error.";
      setBanner({
        message: "Live streaming mode error",
        hint: message,
        category: "warning",
      });
    }
  }, [
    commitLiveUserTranscript,
    finalizeCurrentLiveTurn,
    handleRealtimeFunctionCall,
    requestRealtimeAssistantResponse,
    updateLiveAssistantTranscript,
  ]);

  const startOpenAiRealtimeVoice = useCallback(
    async (session, attemptId) => {
      const clientSecret =
        typeof session?.client_secret === "string" ? session.client_secret : "";
      const connectUrl =
        typeof session?.url === "string" && session.url.trim()
          ? session.url.trim()
          : "https://api.openai.com/v1/realtime/calls";
      const PeerConnectionCtor =
        typeof window !== "undefined"
          ? window.RTCPeerConnection || window.webkitRTCPeerConnection
          : null;

      if (!clientSecret) {
        throw new Error("Realtime session did not include a client secret.");
      }
      if (typeof PeerConnectionCtor !== "function") {
        throw new Error("This browser does not support WebRTC live streaming.");
      }

      const sessionTurnDetection = session?.session?.audio?.input?.turn_detection;
      realtimeTurnDetectionRef.current = {
        type:
          typeof sessionTurnDetection?.type === "string" &&
          sessionTurnDetection.type.trim()
            ? sessionTurnDetection.type.trim()
            : "server_vad",
        interrupt_response: sessionTurnDetection?.interrupt_response !== false,
      };
      realtimePendingToolCallsRef.current = new Set();
      realtimeResponseLifecycleRef.current = {
        active: false,
        requested: false,
      };

      const { stream: localStream } = await createProcessedAudioInput();
      ensureLiveSessionAttemptCurrent(attemptId);
      voiceStreamRef.current = localStream;

      const peer = new PeerConnectionCtor();
      peerConnectionRef.current = peer;
      const videoTransceiver = peer.addTransceiver("video", {
        direction: "sendrecv",
      });
      liveVisualSenderRef.current = videoTransceiver.sender;

      const audioElement = document.createElement("audio");
      audioElement.autoplay = true;
      audioElement.playsInline = true;
      audioElement.setAttribute("aria-hidden", "true");
      audioElement.style.display = "none";
      audioElement.volume = outputVolume;
      audioElement.onplaying = () => setSpeaking(true);
      audioElement.onpause = () => setSpeaking(false);
      audioElement.onended = () => setSpeaking(false);
      document.body.appendChild(audioElement);
      remoteAudioRef.current = audioElement;

      peer.ontrack = (event) => {
        const [remoteStream] = event.streams || [];
        if (!remoteStream) return;
        audioElement.srcObject = remoteStream;
        const playPromise = audioElement.play();
        if (playPromise && typeof playPromise.catch === "function") {
          playPromise.catch(() => {});
        }
      };
      peer.onconnectionstatechange = () => {
        if (["failed", "disconnected", "closed"].includes(peer.connectionState)) {
          stopLiveVoiceSession();
        }
      };

      localStream.getTracks().forEach((track) => peer.addTrack(track, localStream));

      const dataChannel = peer.createDataChannel("oai-events");
      voiceChannelRef.current = dataChannel;
      dataChannel.addEventListener("open", () => {
        configureRealtimeTools().catch(() => {});
      });
      dataChannel.addEventListener("message", (event) => {
        handleRealtimeVoiceEvent(event.data);
      });
      dataChannel.addEventListener("close", () => {
        realtimeResponseLifecycleRef.current = {
          active: false,
          requested: false,
        };
        setSpeaking(false);
        if (liveStreamStateRef.current.sessionId) {
          setLiveStreamingPhase("listening");
        }
      });

      const offer = await peer.createOffer();
      ensureLiveSessionAttemptCurrent(attemptId);
      await peer.setLocalDescription(offer);

      const response = await fetch(connectUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${clientSecret}`,
          "Content-Type": "application/sdp",
        },
        body: offer.sdp || "",
      });
      const answerSdp = await response.text();
      if (!response.ok) {
        throw new Error(
          answerSdp || `Realtime connection failed (${response.status}).`,
        );
      }
      ensureLiveSessionAttemptCurrent(attemptId);
      await peer.setRemoteDescription({ type: "answer", sdp: answerSdp });
      ensureLiveSessionAttemptCurrent(attemptId);
      configureRealtimeTools().catch(() => {});

      setSpeaking(false);
      liveStreamStateRef.current = {
        sessionId:
          session?.session_id ||
          session?.session?.id ||
          session?.id ||
          createClientMessageId("realtime-session"),
        currentTurn: null,
      };
      setLiveStreamingTranscript({ user: "", assistant: "" });
      setLiveStreamingPhase("listening");
      setLiveSessionPending(false);
      setRecording(true);
    },
    [
      configureRealtimeTools,
      createProcessedAudioInput,
      ensureLiveSessionAttemptCurrent,
      handleRealtimeVoiceEvent,
      outputVolume,
      stopLiveVoiceSession,
    ],
  );

  const startLiveKitVoice = useCallback(async (session, attemptId) => {
    if (!session?.token) {
      throw new Error("Voice session did not include a LiveKit token.");
    }
    const room = new Room();
    await room.connect(session.url, session.token, {
      autoSubscribe: true,
    });
    ensureLiveSessionAttemptCurrent(attemptId);
    room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      const remoteSpeaking = speakers.some((participant) => !participant.isLocal);
      setSpeaking(remoteSpeaking);
    });
    const { stream } = await createProcessedAudioInput();
    ensureLiveSessionAttemptCurrent(attemptId);
    voiceStreamRef.current = stream;
    await room.localParticipant.publishTrack(stream.getAudioTracks()[0]);
    ensureLiveSessionAttemptCurrent(attemptId);
    roomRef.current = room;
    realtimePendingToolCallsRef.current = new Set();
    realtimeResponseLifecycleRef.current = {
      active: false,
      requested: false,
    };
    liveStreamStateRef.current = {
      sessionId: createClientMessageId("livekit-session"),
      currentTurn: null,
    };
    setLiveStreamingTranscript({ user: "", assistant: "" });
    setLiveStreamingPhase("listening");
    setLiveSessionPending(false);
    setRecording(true);
  }, [createProcessedAudioInput, ensureLiveSessionAttemptCurrent]);

  const toggleCollapseAllTools = useCallback(() => {
    setCollapseAllTools((prev) => !prev);
    setCollapsedTools({});
  }, []);

  const toggleToolCollapse = useCallback(
    (messageId) => {
      if (!messageId) return;
      setCollapsedTools((prev) => {
        const hasOverride = Object.prototype.hasOwnProperty.call(prev, messageId);
        const current = hasOverride ? prev[messageId] : collapseAllTools;
        return {
          ...prev,
          [messageId]: !current,
        };
      });
    },
    [collapseAllTools],
  );

  const speakAssistantMessage = useCallback(
    async (msg) => {
      if (!msg) return;
      const assistantText =
        typeof msg.text === "string"
          ? msg.text
          : typeof msg.content === "string"
            ? msg.content
            : "";
      if (!assistantText.trim()) return;
      const messageId = msg.id || msg.message_id || null;
      const currentAudio = ttsAudioRef.current;
      if (messageId && ttsActiveMessageId === messageId && currentAudio) {
        if (currentAudio.paused) {
          try {
            await currentAudio.play();
            setTtsPlayback((prev) =>
              prev.messageId === messageId ? { ...prev, status: "playing" } : prev,
            );
          } catch (err) {
            console.error("TTS resume failed", err);
            stopTtsPlayback();
          }
        } else {
          currentAudio.pause();
          setTtsPlayback((prev) =>
            prev.messageId === messageId ? { ...prev, status: "paused" } : prev,
          );
        }
        return;
      }
      stopTtsPlayback();
      setTtsActiveMessageId(messageId);
      setTtsPlayback({
        messageId,
        status: "loading",
        currentTime: 0,
        duration: 0,
      });
      try {
        const payload = {
          text: assistantText,
          audio_format: "wav",
        };
        if (typeof state.ttsModel === "string" && state.ttsModel.trim()) {
          payload.model = state.ttsModel.trim();
        }
        if (typeof state.voiceModel === "string" && state.voiceModel.trim()) {
          payload.voice = state.voiceModel.trim();
        }
        const res = await axios.post("/api/voice/tts", payload);
        const audioB64 = res?.data?.audio_b64;
        const contentType = res?.data?.content_type || "audio/wav";
        if (!audioB64) {
          throw new Error("No audio returned from TTS");
        }
        const audio = new Audio(`data:${contentType};base64,${audioB64}`);
        audio.volume = outputVolume;
        ttsAudioRef.current = audio;
        audio.onloadedmetadata = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId
              ? {
                  ...prev,
                  duration: audio.duration || prev.duration || 0,
                  currentTime: audio.currentTime || 0,
                }
              : prev,
          );
        };
        audio.ontimeupdate = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId
              ? {
                  ...prev,
                  currentTime: audio.currentTime || 0,
                  duration: audio.duration || prev.duration || 0,
                  status: audio.paused ? "paused" : "playing",
                }
              : prev,
          );
        };
        audio.onplay = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId ? { ...prev, status: "playing" } : prev,
          );
        };
        audio.onpause = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId ? { ...prev, status: "paused" } : prev,
          );
        };
        audio.onended = () => {
          stopTtsPlayback();
        };
        audio.onerror = () => {
          stopTtsPlayback();
        };
        await audio.play();
        setTtsPlayback((prev) =>
          prev.messageId === messageId ? { ...prev, status: "playing" } : prev,
        );
      } catch (err) {
        console.error("TTS playback failed", err);
        setBanner({
          message: "TTS playback failed",
          hint:
            err?.response?.data?.detail ||
            err?.message ||
            "Unable to synthesize audio.",
          category: "warning",
        });
        stopTtsPlayback();
      }
    },
    [outputVolume, state.ttsModel, state.voiceModel, stopTtsPlayback, ttsActiveMessageId],
  );
  const clearActiveRequest = useCallback(() => {
    if (activeRequestRef.current) {
      activeRequestRef.current = null;
    }
  }, []);
  const abortActiveRequest = useCallback((reason = "user_cancelled") => {
    if (activeRequestRef.current && typeof activeRequestRef.current.abort === "function") {
      const controller = activeRequestRef.current;
      if (!controller.signal?.aborted) {
        controller.abort(reason);
      }
    }
  }, []);
  const isUserCancelledError = (err) =>
    Boolean(
      err &&
        (err.code === "USER_CANCELLED" ||
          err.cancelled === true ||
          err?.message === "Generation cancelled"),
    );
  const buildToolOutcomeResult = useCallback((status, message, data = null, ok = null) => {
    const normalized = String(status || "").toLowerCase();
    const resolvedOk =
      typeof ok === "boolean" ? ok : normalized && !["error", "denied"].includes(normalized);
    return {
      status,
      ok: Boolean(resolvedOk),
      message: message ?? null,
      data,
    };
  }, []);
  const attachmentCount = attachments.length;
  const historyLength = Array.isArray(state.history) ? state.history.length : 0;
  const computeAdaptiveTimeoutMs = useCallback(
    (text = "", attempt = 0) => {
      const baseMs = baseTimeoutSec * 1000;
      const trimmed = typeof text === "string" ? text.trim() : "";
      const charCount = trimmed.length;
      const wordCount = trimmed ? trimmed.split(/\s+/).length : 0;
      const tokenEstimate = Math.max(charCount / 3.5, wordCount * 0.75);
      const tokenBuckets = Math.max(1, Math.ceil(tokenEstimate / 400));
      const backendFactor =
        state.backendMode === "local"
          ? 3
          : state.backendMode === "server"
          ? 2.4
          : 1.6;
      const attachmentFactor = attachmentCount
        ? Math.min(3, 1 + attachmentCount * 0.35)
        : 1;
      const historyFactor = Math.max(1, Math.ceil(historyLength / 6));
      const attemptFactor = 1 + attempt * 0.75;
      const estimated =
        baseMs *
        tokenBuckets *
        backendFactor *
        attachmentFactor *
        attemptFactor *
        historyFactor;
      const minMs = Math.max(baseMs * 1.5, 20000);
      const idleAllowance = idleTimeoutSec * 1000;
      const maxMs = Math.max(baseMs * 10, idleAllowance, 300000);
      const bounded = Math.min(Math.max(Math.round(estimated), minMs), maxMs);
      return bounded;
    },
    [attachmentCount, baseTimeoutSec, historyLength, idleTimeoutSec, state.backendMode],
  );
  const startComposerResize = useCallback(
    (startY, pointerType = "mouse") => {
      if (cameraOpen) return;
      const rootStyle =
        typeof window !== "undefined" && typeof window.getComputedStyle === "function"
          ? window.getComputedStyle(document.documentElement)
          : null;
      const topbarRaw = rootStyle?.getPropertyValue("--topbar-total-height") || "";
      const topbarHeight = Number.parseFloat(topbarRaw);
      const minRows = DEFAULT_COMPOSER_ROWS;
      const textarea = composerInputRef.current;
      const textareaRect =
        textarea && typeof textarea.getBoundingClientRect === "function"
          ? textarea.getBoundingClientRect()
          : null;
      const inputBoxRect =
        inputBoxRef.current && typeof inputBoxRef.current.getBoundingClientRect === "function"
          ? inputBoxRef.current.getBoundingClientRect()
          : null;
      const lineHeightRaw =
        textarea && typeof window !== "undefined"
          ? window.getComputedStyle(textarea).lineHeight
          : "";
      const lineHeight = Math.max(18, Number.parseFloat(lineHeightRaw) || 24);
      const currentTextareaHeight =
        textareaRect && Number.isFinite(textareaRect.height) && textareaRect.height > 0
          ? textareaRect.height
          : lineHeight * Math.max(composerRows, minRows);
      const chromeHeight =
        inputBoxRect && Number.isFinite(inputBoxRect.height) && inputBoxRect.height > 0
          ? Math.max(0, inputBoxRect.height - currentTextareaHeight)
          : 220;
      const viewportHeight =
        typeof window !== "undefined" && Number.isFinite(window.innerHeight)
          ? window.innerHeight
          : 900;
      const availableTextareaHeight = Math.max(
        lineHeight * minRows,
        viewportHeight - (Number.isFinite(topbarHeight) ? topbarHeight : 96) - 24 - chromeHeight,
      );
      const maxRows = Math.max(
        minRows,
        Math.min(MAX_COMPOSER_ROWS, Math.floor(availableTextareaHeight / lineHeight)),
      );
      const startRows = composerRows;
      const updateRows = (currentY) => {
        if (typeof currentY !== "number") return;
        const deltaY = startY - currentY;
        const deltaRows = Math.round(deltaY / 12);
        const nextRows = Math.min(maxRows, Math.max(minRows, startRows + deltaRows));
        setComposerRows(nextRows);
      };
      const moveEvent = pointerType === "touch" ? "touchmove" : "mousemove";
      const upEvent = pointerType === "touch" ? "touchend" : "mouseup";
      if (typeof document !== "undefined") {
        document.body.style.cursor = "ns-resize";
        document.body.style.userSelect = "none";
      }
      const onMove = (event) => {
        if (pointerType === "touch") {
          const touch = event.touches && event.touches[0];
          if (!touch) return;
          event.preventDefault();
          updateRows(touch.clientY);
        } else {
          event.preventDefault();
          updateRows(event.clientY);
        }
      };
      const onUp = () => {
        window.removeEventListener(moveEvent, onMove);
        window.removeEventListener(upEvent, onUp);
        if (pointerType === "touch") {
          window.removeEventListener("touchcancel", onUp);
        }
        if (typeof document !== "undefined") {
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
        }
      };
      window.addEventListener(moveEvent, onMove, pointerType === "touch" ? { passive: false } : undefined);
      window.addEventListener(upEvent, onUp);
      if (pointerType === "touch") {
        window.addEventListener("touchcancel", onUp);
      }
    },
    [cameraOpen, composerRows],
  );
  const handleComposerResizeKeyDown = useCallback(
    (event) => {
      if (cameraOpen) return;
      const step = event.shiftKey ? 4 : 1;
      if (event.key === "Home") {
        event.preventDefault();
        setComposerRows(DEFAULT_COMPOSER_ROWS);
        return;
      }
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      setComposerRows((prev) => {
        const next = event.key === "ArrowUp" ? prev + step : prev - step;
        return Math.max(DEFAULT_COMPOSER_ROWS, Math.min(MAX_COMPOSER_ROWS, next));
      });
    },
    [cameraOpen],
  );

  const requestModelCompletion = useCallback(
    async (payload, text, options = {}) => {
      const { trackAbort = true, endpoint = "/api/llm/generate" } = options;
      const attemptRequest = async (attemptIndex) => {
        const timeoutMs = computeAdaptiveTimeoutMs(text, attemptIndex);
        const canAbort = typeof AbortController !== "undefined";
        const controller = canAbort ? new AbortController() : null;
        if (controller && trackAbort) {
          activeRequestRef.current = controller;
        }
        const timer = setTimeout(() => {
          if (controller) {
            controller.abort("timeout");
          }
        }, timeoutMs);
        try {
          const response = await axios.post(
            endpoint,
            payload,
            controller
              ? { signal: controller.signal, timeout: 0 }
              : { timeout: timeoutMs },
          );
          return response;
        } catch (error) {
          const abortedReason =
            controller && controller.signal?.aborted
              ? controller.signal.reason
              : null;
          const abortedByUser =
            abortedReason === "user_cancelled" ||
            error?.code === "USER_CANCELLED" ||
            error?.cancelled === true;
          if (abortedByUser) {
            const userCancelError = new Error("Generation cancelled");
            userCancelError.code = "USER_CANCELLED";
            userCancelError.cause = error;
            throw userCancelError;
          }
          const axiosCancelled =
            error?.code === "ERR_CANCELED" || error?.name === "CanceledError";
          const abortedByTimeout =
            abortedReason === "timeout" ||
            axiosCancelled ||
            error?.code === "ECONNABORTED" ||
            (typeof error?.message === "string" &&
              error.message.toLowerCase().includes("timeout"));
          if (abortedByTimeout) {
            const timeoutError = new Error(
              `Stopped waiting after ${Math.round(timeoutMs / 1000)}s. Try again or adjust model settings.`,
            );
            timeoutError.code = "REQUEST_TIMEOUT";
            timeoutError.timeoutMs = timeoutMs;
            timeoutError.cause = error;
            throw timeoutError;
          }
          throw error;
        } finally {
          clearTimeout(timer);
          if (controller && activeRequestRef.current === controller) {
            clearActiveRequest();
          }
        }
      };
      const shouldRetry = (err) => {
        if (!err) return false;
        if (isUserCancelledError(err)) {
          return false;
        }
        if (err.code === "REQUEST_TIMEOUT") {
          return true;
        }
        if (err.response && err.response.status) {
          const status = err.response.status;
          return status >= 500 || status === 429;
        }
        return false;
      };
      try {
        return await attemptRequest(0);
      } catch (err) {
        if (!shouldRetry(err)) {
          throw err;
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
        return attemptRequest(1);
      }
    },
    [clearActiveRequest, computeAdaptiveTimeoutMs, isUserCancelledError],
  );
  useEffect(() => {
    if (window.mermaid) {
      window.mermaid.initialize({ startOnLoad: false });
    }
  }, []);

  // Honor backend-provided auto titles on any conversation entry
  useEffect(() => {
    if (!Array.isArray(state.conversation) || !state.conversation.length) return;
    for (let i = state.conversation.length - 1; i >= 0; i -= 1) {
      const meta = state.conversation[i]?.metadata;
      if (!meta || typeof meta !== "object") continue;
      const candidate =
        meta.session_display_name || meta.display_name || meta.session_title;
      if (typeof candidate === "string" && candidate.trim()) {
        applySessionDisplayName(candidate.trim());
        break;
      }
    }
  }, [state.conversation, applySessionDisplayName]);

  // helper: truncate filename to ~15 chars, keeping extension visible when possible
  const truncateFilename = (name, limit = 15) => {
    if (!name || name.length <= limit) return name;
    const ellipsis = "\u2026";
    const dot = name.lastIndexOf(".");
    if (dot > 0 && dot < name.length - 1) {
      const base = name.slice(0, dot);
      const ext = name.slice(dot);
      if (base.length > limit - 3) {
        return `${base.slice(0, limit - 3)}${ellipsis}${ext}`;
      }
    }
    return `${name.slice(0, limit - 1)}${ellipsis}`;
  };

  const toAbsoluteUrl = (path) => {
    if (!path) return null;
    if (/^https?:\/\//i.test(path)) return path;
    try {
      if (typeof window === "undefined") return path;
      return new URL(path, window.location.origin).toString();
    } catch (err) {
      return path;
    }
  };

  const resolveScrollContainer = () => {
    const primary = chatBoxRef.current;
    const fallback =
      (primary && primary.closest(".main-chat")) || document.querySelector(".main-chat");
    const docScroller =
      typeof document !== "undefined" ? document.scrollingElement : null;
    const candidates = [primary, fallback, docScroller].filter(Boolean);
    let best = primary || fallback || docScroller || null;
    let bestOverflow = -1;
    candidates.forEach((candidate) => {
      const overflow = candidate.scrollHeight - candidate.clientHeight;
      if (overflow > bestOverflow) {
        best = candidate;
        bestOverflow = overflow;
      }
    });
    return best;
  };

  const scrollToBottom = (behavior = "auto") => {
    const node = resolveScrollContainer();
    if (!node) return;
    if (typeof node.scrollTo === "function") {
      node.scrollTo({ top: node.scrollHeight, behavior });
      if (behavior === "smooth") {
        setTimeout(() => {
          node.scrollTo({ top: node.scrollHeight, behavior: "auto" });
        }, 220);
      }
    } else {
      node.scrollTop = node.scrollHeight;
    }
    setIsAtBottom(true);
  };

  const scrollMessageIntoView = useCallback(
    (messageId, behavior = "smooth") => {
      if (!messageId || !messageRefs.current[messageId]) return;
      const node = resolveScrollContainer();
      const target = messageRefs.current[messageId];
      if (!node || !target || typeof target.getBoundingClientRect !== "function") {
        target?.scrollIntoView?.({ behavior, block: "end" });
        return;
      }
      const containerRect = node.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const absoluteBottom =
        targetRect.bottom - containerRect.top + node.scrollTop;
      const bottomInset = Math.min(96, Math.max(32, node.clientHeight * 0.18));
      const nextTop = Math.max(
        0,
        absoluteBottom - (node.clientHeight - bottomInset),
      );
      if (typeof node.scrollTo === "function") {
        node.scrollTo({ top: nextTop, behavior });
      } else {
        node.scrollTop = nextTop;
      }
    },
    [],
  );

  const scheduleScrollToBottom = (behavior = "auto") => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => scrollToBottom(behavior));
    } else {
      setTimeout(() => scrollToBottom(behavior), 0);
    }
  };

  useEffect(() => {
    const primary = chatBoxRef.current;
    const fallback =
      (primary && primary.closest(".main-chat")) || document.querySelector(".main-chat");
    const docScroller =
      typeof document !== "undefined" ? document.scrollingElement : null;
    const candidates = [primary, fallback, docScroller].filter(Boolean);
    if (!candidates.length) return undefined;

    const thresholdPx = 48;
    const update = () => {
      const active = resolveScrollContainer();
      if (!active) return;
      const distanceFromBottom =
        active.scrollHeight - active.scrollTop - active.clientHeight;
      setIsAtBottom(distanceFromBottom <= thresholdPx);
    };

    update();
    candidates.forEach((candidate) => {
      candidate.addEventListener("scroll", update, { passive: true });
    });
    window.addEventListener("resize", update);
    return () => {
      candidates.forEach((candidate) => {
        candidate.removeEventListener("scroll", update);
      });
      window.removeEventListener("resize", update);
    };
  }, [state.sessionId]);

  // Keep chat auto-scroll aligned above the floating composer by dynamically
  // updating the global `--input-offset` variable based on the composer height.
  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }

    const root = document.documentElement;
    if (!root || !root.style) return undefined;

    const schedule = (fn) => {
      if (inputOffsetRafRef.current) {
        if (typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(inputOffsetRafRef.current);
        }
        inputOffsetRafRef.current = null;
      }
      if (inputOffsetTimerRef.current) {
        clearTimeout(inputOffsetTimerRef.current);
        inputOffsetTimerRef.current = null;
      }

      if (typeof requestAnimationFrame === "function") {
        inputOffsetRafRef.current = requestAnimationFrame(fn);
      } else {
        inputOffsetTimerRef.current = setTimeout(fn, 0);
      }
    };

    const update = () => {
      schedule(() => {
        inputOffsetRafRef.current = null;
        inputOffsetTimerRef.current = null;

        const box = entryOpen
          ? inputBoxRef.current
          : (typeof document !== "undefined"
            ? document.querySelector(".open-entry-btn")
            : null);
        if (!box || typeof box.getBoundingClientRect !== "function") {
          const fallback = entryOpen ? 148 : 72;
          if (inputOffsetRef.current !== fallback) {
            inputOffsetRef.current = fallback;
            root.style.setProperty("--input-offset", `${fallback}px`);
            if (isAtBottom) scrollToBottom("auto");
          }
          return;
        }

        const rect = box.getBoundingClientRect();
        const height = Number.isFinite(rect.height) ? rect.height : 0;
        // Extra cushion for the composer's bottom gap, shadows, and mobile safe-area.
        const minOffset = entryOpen ? 72 : 56;
        const extra = entryOpen ? 28 : 16;
        const next = Math.max(minOffset, Math.ceil(height + extra));
        if (inputOffsetRef.current === next) return;
        inputOffsetRef.current = next;
        root.style.setProperty("--input-offset", `${next}px`);
        if (isAtBottom) scrollToBottom("auto");
      });
    };

    update();

    let observer = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => update());
      const target = entryOpen
        ? inputBoxRef.current
        : (typeof document !== "undefined"
          ? document.querySelector(".open-entry-btn")
          : null);
      if (target) observer.observe(target);
    }

    const onResize = () => update();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      if (observer) observer.disconnect();
      if (inputOffsetRafRef.current) {
        if (typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(inputOffsetRafRef.current);
        }
        inputOffsetRafRef.current = null;
      }
      if (inputOffsetTimerRef.current) {
        clearTimeout(inputOffsetTimerRef.current);
        inputOffsetTimerRef.current = null;
      }
    };
  }, [attachments.length, composerRows, entryOpen, isAtBottom, state.sessionId]);

  useEffect(() => {
    if (isAtBottom) {
      scrollToBottom();
    }
  }, [state.conversation, isAtBottom]);

  useEffect(() => {
    if (!state || !state.sessionId) return;
    initialScrollRef.current = false;
  }, [state.sessionId]);

  useEffect(() => {
    setCollapseAllTools(true);
    setCollapsedTools({});
  }, [state.sessionId]);

  useEffect(() => {
    if (initialScrollRef.current) return;
    if (!state.conversation || state.conversation.length === 0) return;
    const timer = setTimeout(() => {
      if (!initialScrollRef.current) {
        scrollToBottom("auto");
        initialScrollRef.current = true;
      }
    }, 30);
    return () => clearTimeout(timer);
  }, [state.conversation?.length]);

  useEffect(() => {
    if (!messageDelta || !activeMessageId) return;
    setState((prev) => {
      const updated = [...prev.conversation];
      const idx = updated.findIndex((m) => m.id === activeMessageId);
      if (idx !== -1) {
        const msg = { ...updated[idx] };
        if (messageDelta.type === "thought") {
          const nextThoughts = appendThoughtChunk(msg.thoughts, messageDelta.content);
          if (nextThoughts !== msg.thoughts) {
            msg.thoughts = nextThoughts;
          }
        } else if (messageDelta.type === "tool") {
          msg.tools = [...(msg.tools || []), messageDelta];
        } else if (messageDelta.type === "task") {
          msg.tasks = [...(msg.tasks || []), messageDelta];
        }
        updated[idx] = msg;
      }
      return { ...prev, conversation: updated };
    });
  }, [messageDelta, activeMessageId, setState]);

  useEffect(() => {
    if (activeMessageId && messageRefs.current[activeMessageId]) {
      scrollMessageIntoView(activeMessageId, "smooth");
    }
  }, [activeMessageId, scrollMessageIntoView]);

  useEffect(() => {
    if (typeof document === "undefined" || !setActiveMessageId) return undefined;
    const handleOutsideClick = (event) => {
      const target = event?.target;
      if (!(target instanceof Element)) return;
      if (target.closest(".user-msg") || target.closest(".ai-msg")) return;
      setActiveMessageId(null);
    };
    document.addEventListener("click", handleOutsideClick);
    return () => document.removeEventListener("click", handleOutsideClick);
  }, [setActiveMessageId]);

  useEffect(() => {
    if (!activeBrowserSession?.sessionId) return;
    setBrowserNavigateDraft(activeBrowserSession.currentUrl || "");
  }, [activeBrowserSession?.currentUrl, activeBrowserSession?.sessionId]);

  useEffect(() => {
    if (!browserSessionPopup) return undefined;
    const handleEscape = (event) => {
      if (event.key !== "Escape") return;
      setBrowserSessionPopup(null);
      setBrowserPopupError("");
      setBrowserPopupPendingAction("");
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [browserSessionPopup]);

  // Ensure only one hover timer is ever active and clear on state changes
  useEffect(() => {
    if (entryHoverTimer.current) {
      clearTimeout(entryHoverTimer.current);
      entryHoverTimer.current = null;
    }
    return () => {
      if (entryHoverTimer.current) {
        clearTimeout(entryHoverTimer.current);
        entryHoverTimer.current = null;
      }
    };
  }, [entryOpen]);

  const focusComposerInput = useCallback(() => {
    const candidate =
      composerInputRef.current ||
      inputBoxRef.current?.querySelector("textarea, input");
    if (!candidate || typeof candidate.focus !== "function") return;
    candidate.focus();
    if (
      typeof candidate.value === "string" &&
      typeof candidate.setSelectionRange === "function"
    ) {
      const end = candidate.value.length;
      candidate.setSelectionRange(end, end);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handleNewChat = () => {
      setEntryOpen(true);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          focusComposerInput();
        });
      });
    };
    window.addEventListener("float:new-chat", handleNewChat);
    return () => window.removeEventListener("float:new-chat", handleNewChat);
  }, [focusComposerInput]);

  useEffect(() => {
    if (!entryOpen) return;
    const conversationLength = Array.isArray(state.conversation)
      ? state.conversation.length
      : 0;
    if (conversationLength !== 0) return;
    const timer = window.setTimeout(() => {
      focusComposerInput();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [entryOpen, focusComposerInput, state.conversation?.length, state.sessionId]);

  const attachmentLooksImage = useCallback((attachment) => {
    if (!attachment || typeof attachment !== "object") return false;
    const contentType = String(
      attachment.file?.type || attachment.type || attachment.content_type || "",
    )
      .trim()
      .toLowerCase();
    if (contentType.startsWith("image/")) return true;
    const candidateName =
      attachment.file?.name || attachment.name || attachment.filename || attachment.url || "";
    return /\.(png|jpe?g|gif|webp|svg)$/i.test(String(candidateName));
  }, []);

  const revokeAttachmentPreview = useCallback((attachment) => {
    if (!attachment?.url) return;
    try {
      URL.revokeObjectURL(attachment.url);
    } catch (_) {}
  }, []);

  const clearComposerAttachments = useCallback(() => {
    setAttachments((prev) => {
      prev.forEach((attachment) => revokeAttachmentPreview(attachment));
      return [];
    });
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    setVisionWorkflow("auto");
  }, [revokeAttachmentPreview]);

  const stopCameraCapture = useCallback(() => {
    const stream = cameraStreamRef.current;
    if (stream && typeof stream.getTracks === "function") {
      stream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch (_) {}
      });
    }
    cameraStreamRef.current = null;
    if (cameraVideoRef.current) {
      try {
        cameraVideoRef.current.srcObject = null;
      } catch (_) {}
    }
    setCameraError("");
    setCameraOpen(false);
  }, []);

  useEffect(() => () => stopCameraCapture(), [stopCameraCapture]);

  useEffect(() => {
    if (!cameraOpen || !cameraVideoRef.current || !cameraStreamRef.current) return undefined;
    const video = cameraVideoRef.current;
    video.srcObject = cameraStreamRef.current;
    const playAttempt = video.play();
    if (playAttempt && typeof playAttempt.catch === "function") {
      playAttempt.catch(() => {});
    }
    return () => {
      if (video.srcObject) {
        try {
          video.srcObject = null;
        } catch (_) {}
      }
    };
  }, [cameraOpen]);

  useEffect(() => {
    if (!liveVisualPreviewRef.current || !liveVisualStreamRef.current) return undefined;
    const video = liveVisualPreviewRef.current;
    video.srcObject = liveVisualStreamRef.current;
    const playAttempt = video.play();
    if (playAttempt && typeof playAttempt.catch === "function") {
      playAttempt.catch(() => {});
    }
    return () => {
      if (video.srcObject) {
        try {
          video.srcObject = null;
        } catch (_) {}
      }
    };
  }, [liveVisualMode, recording]);

  useEffect(() => {
    if (!chatSettingsOpen) return undefined;
    refreshAvailableInputDevices().catch(() => {});
    const mediaDevices = navigator?.mediaDevices;
    if (!mediaDevices || typeof mediaDevices.addEventListener !== "function") {
      return undefined;
    }
    const handleDeviceChange = () => {
      refreshAvailableInputDevices().catch(() => {});
    };
    mediaDevices.addEventListener("devicechange", handleDeviceChange);
    return () => {
      mediaDevices.removeEventListener("devicechange", handleDeviceChange);
    };
  }, [chatSettingsOpen, refreshAvailableInputDevices]);

  useEffect(() => {
    if (!chatSettingsOpen) return undefined;
    const handlePointerDown = (event) => {
      const target = event.target;
      if (chatSettingsMenuRef.current?.contains(target)) return;
      if (chatSettingsPopoverRef.current?.contains(target)) return;
      setChatSettingsOpen(false);
    };
    const handleEscape = (event) => {
      if (event.key === "Escape") {
        setChatSettingsOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [chatSettingsOpen]);

  useEffect(() => {
    if (!attachmentMenuOpen) return undefined;
    const handlePointerDown = (event) => {
      const target = event.target;
      if (attachmentMenuRef.current?.contains(target)) return;
      setAttachmentMenuOpen(false);
    };
    const handleEscape = (event) => {
      if (event.key === "Escape") {
        setAttachmentMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [attachmentMenuOpen]);

  useEffect(() => {
    if (!chatSettingsOpen) {
      setChatSettingsPopoverStyle(null);
      return undefined;
    }
    let frameId = null;
    const syncPosition = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        updateChatSettingsPopoverPosition();
      });
    };
    syncPosition();
    window.addEventListener("resize", syncPosition);
    window.addEventListener("scroll", syncPosition, true);
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.removeEventListener("resize", syncPosition);
      window.removeEventListener("scroll", syncPosition, true);
    };
  }, [chatSettingsOpen, updateChatSettingsPopoverPosition]);

  useEffect(() => {
    if (remoteAudioRef.current) {
      remoteAudioRef.current.volume = outputVolume;
    }
    if (ttsAudioRef.current) {
      ttsAudioRef.current.volume = outputVolume;
    }
  }, [outputVolume]);

  useEffect(() => () => stopMicTest(), [stopMicTest]);

  const openCameraCapture = useCallback(async () => {
    if (cameraOpen) {
      stopCameraCapture();
      return;
    }
    if (!navigator?.mediaDevices?.getUserMedia) {
      setCameraError("Camera capture is not available in this browser.");
      return;
    }
    setCameraBusy(true);
    setCameraError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: buildCameraConstraints(),
        audio: false,
      });
      cameraStreamRef.current = stream;
      setComposerRows(DEFAULT_COMPOSER_ROWS);
      setCameraOpen(true);
      refreshAvailableInputDevices().catch(() => {});
    } catch (err) {
      console.error("camera open failed", err);
      setCameraError("Camera access failed.");
    } finally {
      setCameraBusy(false);
    }
  }, [buildCameraConstraints, cameraOpen, refreshAvailableInputDevices, stopCameraCapture]);

  const buildDefaultPrompt = useCallback(
    (workflow, imageCount, attachmentTotal) => {
      if (attachmentTotal <= 0) return "";
      if (imageCount > 0) {
        if (workflow === "ocr") return "Read any visible text in the attached image.";
        if (workflow === "compare") return "Compare the attached images.";
        if (workflow === "caption") return "Describe the attached image.";
        if (workflow === "image_qa") return "Answer using the attached image.";
        return imageCount > 1
          ? "Analyze the attached images."
          : "Analyze the attached image.";
      }
      return attachmentTotal > 1
        ? "Use the attached files in your response."
        : "Use the attached file in your response.";
    },
    [],
  );

  const imageAttachmentCount = useMemo(
    () => attachments.filter((attachment) => attachmentLooksImage(attachment)).length,
    [attachmentLooksImage, attachments],
  );
  const hasImageAttachments = imageAttachmentCount > 0;
  const effectiveComposerRows = cameraOpen ? DEFAULT_COMPOSER_ROWS : composerRows;
  const selectedVisionWorkflow =
    VISION_WORKFLOW_OPTIONS.find((option) => option.value === visionWorkflow) ||
    VISION_WORKFLOW_OPTIONS[0];

  useEffect(() => {
    if (!hasImageAttachments && visionWorkflow !== "auto") {
      setVisionWorkflow("auto");
    }
  }, [hasImageAttachments, visionWorkflow]);

  const sendMessage = async (msg = message) => {
    setAttachmentMenuOpen(false);
    const trimmedMessage = typeof msg === "string" ? msg.trim() : "";
    const effectiveVisionWorkflow = hasImageAttachments ? visionWorkflow : "auto";
    if (effectiveVisionWorkflow === "compare" && imageAttachmentCount < 2) {
      setError("Compare mode needs at least two image attachments.");
      return;
    }
    const effectiveMessage =
      trimmedMessage ||
      buildDefaultPrompt(effectiveVisionWorkflow, imageAttachmentCount, attachments.length);
    if (!effectiveMessage) return;
    if (attachments.some((a) => a.uploading)) {
      setError("Attachments are still uploading. Please wait.");
      return;
    }
    abortActiveRequest();
    clearActiveRequest();
    const normalizedAttachments = attachments.map((a) => {
      const name = a.file?.name || a.name || "attachment";
      const type = a.file?.type || a.type || "";
      const size = typeof a.file?.size === "number" ? a.file.size : a.size;
      const remote = a.remoteUrl ? toAbsoluteUrl(a.remoteUrl) : null;
      const fallbackUrl = a.url || a.src || null;
      const url = remote || fallbackUrl;
      const contentHash = a.contentHash || a.content_hash || null;
      return {
        name,
        type,
        url,
        size,
        remoteUrl: remote,
        content_hash: contentHash,
        origin: a.origin || null,
        relative_path: a.relative_path || a.relativePath || null,
        capture_source: a.capture_source || a.captureSource || null,
        capture_id: a.capture_id || a.captureId || null,
        transient: a.transient === true,
        expires_at: a.expires_at || null,
        caption_status: a.caption_status || null,
        index_status: a.index_status || null,
        placeholder_caption: a.placeholder_caption ?? null,
      };
    });
    const conversationAttachments = normalizedAttachments
      .map(
        ({
          name,
          type,
          url,
          size,
          content_hash,
          origin,
           relative_path,
           capture_source,
           capture_id,
           transient,
           expires_at,
           caption_status,
           index_status,
           placeholder_caption,
         }) => ({
        name,
        type,
        url,
        size,
        content_hash,
          origin,
           relative_path,
           capture_source,
           capture_id,
           transient,
           expires_at,
           caption_status,
           index_status,
           placeholder_caption,
        }),
      )
      .filter((att) => !!att.url);
    const apiAttachments = normalizedAttachments
      .filter((att) => !!att.remoteUrl)
      .map(
        ({
          name,
          type,
          remoteUrl,
          size,
           content_hash,
           origin,
           relative_path,
           capture_source,
           capture_id,
           transient,
           expires_at,
         }) => ({
        name,
        type,
        url: remoteUrl,
        size,
        content_hash,
           origin,
           relative_path,
           capture_source,
           capture_id,
           transient,
           expires_at,
        }),
      );
    // Do not block chat when API provider check is offline; attempt anyway.
    // The backend handles missing keys/providers and returns a helpful message.
    if (state.backendMode === "api" && state.apiStatus !== "online") {
      console.warn("API provider not ready; attempting chat anyway");
    }
    setError(null);
    setLoading(true);
    setIsStreaming(true);
    const msgId = crypto.randomUUID();
    setActiveMessageId && setActiveMessageId(msgId);
    console.log("Sending message:", effectiveMessage);

    try {
      // Ensure device token for any sync-enabled features
      if (state.backendMode === "api") {
        await ensureDeviceAndToken();
      }
      memoryStore["last_message"] = { content: effectiveMessage, importance: 5 };
    setState((prev) => {
      const newHistory = [...prev.history, { role: "user", text: effectiveMessage }];
        const attachmentsForState = conversationAttachments.map((att) => ({ ...att }));
        const timestampIso = new Date().toISOString();
        const newState = {
          ...prev,
          conversation: [
            ...prev.conversation,
            {
              id: `${msgId}:user`,
              role: "user",
              text: effectiveMessage,
              timestamp: timestampIso,
              attachments: attachmentsForState,
              metadata:
                hasImageAttachments || effectiveVisionWorkflow !== "auto"
                  ? { vision: { workflow: effectiveVisionWorkflow } }
                  : undefined,
            },
            {
              role: "ai",
              id: msgId,
              text: "",
              thoughts: [],
              tools: [],
              timestamp: timestampIso,
              metadata: { status: "pending" },
            },
          ],
          history: newHistory,
        };
        localStorage.setItem("history", JSON.stringify(newHistory));
        const payload = JSON.stringify({
          sessionId: prev.sessionId,
          history: newHistory,
        });
        if (typeof navigator !== "undefined" && navigator.sendBeacon) {
          const blob = new Blob([payload], { type: "application/json" });
          navigator.sendBeacon("/api/history", blob);
        } else {
          axios
            .post("/api/history", {
              sessionId: prev.sessionId,
              history: newHistory,
            })
            .catch(() => {});
        }
        return newState;
      });
      setMessage("");
      setComposerRows(DEFAULT_COMPOSER_ROWS);
      scheduleScrollToBottom("smooth");
      let aiResponse = "";
      let responseMetadata = null;
      let ragMatchesFromResponse = [];
      let responseThought = "";
      let responseTools = [];
      if (state.backendMode === "api") {
        const controller =
          typeof AbortController !== "undefined" ? new AbortController() : null;
        if (controller) {
          activeRequestRef.current = controller;
        }
        try {
          let res = await apiWrapper.chat(
            {
              message: effectiveMessage,
              session_id: state.sessionId,
              model: state.apiModel,
              message_id: msgId,
              attachments: apiAttachments,
              vision_workflow: effectiveVisionWorkflow,
              ...thinkingPayload,
            },
            { signal: controller?.signal },
          );
          console.log("API Response:", res);
          // quick client-side retry for transient errors
          if (res?.cancelled) {
            const userCancelError = new Error("Generation cancelled");
            userCancelError.code = "USER_CANCELLED";
            throw userCancelError;
          }
          if (res.error) {
            await new Promise((r) => setTimeout(r, 400));
            res = await apiWrapper.chat(
              {
                message: effectiveMessage,
                session_id: state.sessionId,
                model: state.apiModel,
                message_id: msgId,
                attachments: apiAttachments,
                vision_workflow: effectiveVisionWorkflow,
                ...workflowPayload,
                ...thinkingPayload,
              },
              { signal: controller?.signal },
            );
            if (res?.cancelled) {
              const userCancelError = new Error("Generation cancelled");
              userCancelError.code = "USER_CANCELLED";
              throw userCancelError;
            }
            if (res.error) {
              throw new Error(res.error);
            }
          }
          aiResponse = res.message;
          responseThought = res.thought || "";
          responseTools = Array.isArray(res?.tools_used) ? res.tools_used : [];
          const md = res.metadata || {};
          responseMetadata = Object.keys(md).length ? md : null;
          ragMatchesFromResponse = ragMatchesFromSection(md?.rag);
          if (md.error || md.warning) {
            const actions = [
              { label: "Settings", onClick: () => navigate("/settings") },
              { label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) },
            ];
            setBanner({ message: md.warning || md.error, hint: md.hint, category: md.category, actions });
          } else {
            setBanner(null);
          }
        } finally {
          if (controller && activeRequestRef.current === controller) {
            clearActiveRequest();
          }
        }
      } else if (state.backendMode === "local" || state.backendMode === "server") {
        const mode = state.backendMode;
        const model = mode === "local" ? (state.localModel || state.transformerModel) : (state.transformerModel || state.apiModel);
        const payload = {
          message: effectiveMessage,
          mode,
          session_id: state.sessionId,
          message_id: msgId,
          model,
          attachments: apiAttachments,
          vision_workflow: effectiveVisionWorkflow,
          ...workflowPayload,
          ...thinkingPayload,
        };
        const r = await requestModelCompletion(payload, effectiveMessage, {
          trackAbort: true,
          endpoint: "/api/chat",
        });
        aiResponse = r.data?.message || "";
        responseThought = r.data?.thought || "";
        responseTools = Array.isArray(r.data?.tools_used) ? r.data.tools_used : [];
        const responseMeta = r.data?.metadata || {};
        responseMetadata = Object.keys(responseMeta).length ? responseMeta : null;
        ragMatchesFromResponse = ragMatchesFromSection(responseMeta?.rag);
        if (responseMeta?.error || responseMeta?.warning) {
          const actions = [{ label: "Settings", onClick: () => navigate("/settings") }];
          if (mode === "server") {
            actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
          }
          setBanner({
            message: responseMeta.warning || responseMeta.error,
            hint:
              responseMeta.hint ||
              (mode === "server"
                ? "Verify the Server URL and model in Settings (and Harmony formatting for GPT-OSS, e.g. gpt-oss-20b/120b)."
                : "Verify the local model is available and settings are correct."),
            category: responseMeta.category || (mode === "server" ? "server_error" : "local_error"),
            actions,
          });
        } else {
          setBanner(null);
        }
      }
      const metadataDisplayName =
        responseMetadata &&
        (responseMetadata.session_display_name ||
          responseMetadata.display_name ||
          responseMetadata.session_title);
      if (metadataDisplayName) {
        applySessionDisplayName(metadataDisplayName);
      }

      memoryStore["last_ai_response"] = { content: aiResponse, importance: 4 };
      setState((prev) => {
        const updatedConversation = [...prev.conversation];
        const idx = updatedConversation.findIndex((m) => m && m.id === msgId);
        if (idx !== -1) {
          const entry = { ...updatedConversation[idx], text: aiResponse };
          if (responseMetadata && Object.keys(responseMetadata).length) {
            entry.metadata = { ...(entry.metadata || {}), ...responseMetadata };
          }
          if (typeof responseThought === "string" && responseThought.trim()) {
            const trimmed = responseThought.trim();
            const thoughts = Array.isArray(entry.thoughts) ? [...entry.thoughts] : [];
            const normalized = normalizeThoughtText(trimmed);
            const merged = mergeThoughtChunks(thoughts);
            const hasThought = merged.some(
              (item) => normalizeThoughtText(item) === normalized,
            );
            if (normalized && !hasThought) thoughts.push(trimmed);
            entry.thoughts = thoughts;
          }
          const mergedTools = mergeToolEntries(
            entry.tools,
            responseTools,
            responseMetadata,
            { includeInlineMetadata: false },
          );
          if (mergedTools.length) {
            entry.tools = mergedTools;
          }
          if (ragMatchesFromResponse.length) {
            entry.ragMatches = ragMatchesFromResponse;
            const ragSection =
              entry.metadata && entry.metadata.rag && typeof entry.metadata.rag === "object"
                ? { ...entry.metadata.rag }
                : {};
            ragSection.matches = ragMatchesFromResponse;
            entry.metadata = { ...(entry.metadata || {}), rag: ragSection };
          }
          updatedConversation[idx] = entry;
        }
        const newHistory = [...prev.history, { role: "ai", text: aiResponse }];
        localStorage.setItem("history", JSON.stringify(newHistory));
        const payload = JSON.stringify({
          sessionId: prev.sessionId,
          history: newHistory,
        });
        if (typeof navigator !== "undefined" && navigator.sendBeacon) {
          const blob = new Blob([payload], { type: "application/json" });
          navigator.sendBeacon("/api/history", blob);
        } else {
          axios
            .post("/api/history", {
              sessionId: prev.sessionId,
              history: newHistory,
            })
            .catch(() => {});
        }
        return {
          ...prev,
          conversation: updatedConversation,
          history: newHistory,
        };
      });
      clearComposerAttachments();
      stopCameraCapture();
    } catch (err) {
      if (isUserCancelledError(err)) {
        setError(null);
        setBanner(null);
        setState((prev) => ({
          ...prev,
          conversation: prev.conversation.map((entry) =>
            entry && entry.id === msgId
              ? {
                  ...entry,
                  text:
                    entry.text && entry.text.trim()
                      ? entry.text
                      : "(response stopped)",
                  metadata: { ...(entry.metadata || {}), status: "cancelled" },
                }
              : entry,
          ),
        }));
        return;
      }
      const isTimeoutError = err && err.code === "REQUEST_TIMEOUT";
      const detail =
        isTimeoutError
          ? err.message
          : getRequestErrorDetail(err, "Request failed");
      setError(detail);
      if (isTimeoutError) {
        const actions = [{ label: "Settings", onClick: () => navigate("/settings") }];
        if (state.backendMode === "api") {
          actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
        }
        setBanner({
          message: detail,
          hint: "Generation exceeded the current timeout. Try again, simplify the prompt, or raise the limit in Settings.",
          category: "timeout",
          actions,
        });
      } else if (state.backendMode === "api") {
        const actions = [
          { label: "Settings", onClick: () => navigate("/settings") },
          { label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) },
        ];
        setBanner({
          message: detail,
          hint: "Check API key and endpoint or switch to local mode.",
          category: "api_error",
          actions,
        });
      } else {
        setBanner(null);
      }
      console.error("Chat API Error:", err);
      setState((prev) => {
        const updated = Array.isArray(prev.conversation) ? [...prev.conversation] : [];
        const idx = updated.findIndex((entry) => entry && entry.id === msgId);
        const timestampIso = new Date().toISOString();
        const fallbackText = detail ? `(error) ${detail}` : "(error) Request failed";
        if (idx !== -1) {
          const existing = updated[idx] || {};
          const existingText = typeof existing.text === "string" ? existing.text : "";
          updated[idx] = {
            ...existing,
            role: existing.role || "ai",
            id: existing.id || msgId,
            text: existingText && existingText.trim() ? existingText : fallbackText,
            timestamp: timestampIso,
            metadata: {
              ...(existing.metadata || {}),
              status: "error",
              error: detail,
            },
          };
        } else if (msgId) {
          updated.push({
            role: "ai",
            id: msgId,
            text: fallbackText,
            thoughts: [],
            tools: [],
            timestamp: timestampIso,
            metadata: { status: "error", error: detail },
          });
        }
        return { ...prev, conversation: updated };
      });
    } finally {
      setLoading(false);
      setIsStreaming(false);
      setActiveMessageId && setActiveMessageId(null);
      clearActiveRequest();
    }
  }; 

  const cancelGeneration = useCallback(() => {
    abortActiveRequest("user_cancelled");
    clearActiveRequest();
    setLoading(false);
    setIsStreaming(false);
    if (activeMessageId) {
      setState((prev) => ({
        ...prev,
        conversation: prev.conversation.map((entry) =>
          entry && entry.id === activeMessageId
            ? {
                ...entry,
                text:
                  entry.text && entry.text.trim()
                    ? entry.text
                    : "(response stopped)",
                metadata: { ...(entry.metadata || {}), status: "cancelled" },
              }
            : entry,
        ),
      }));
    }
    setActiveMessageId && setActiveMessageId(null);
  }, [abortActiveRequest, activeMessageId, setActiveMessageId, setState]);

  const regenerateMessage = async (msg, options = {}) => {
    const overrideUserText =
      options && typeof options.overrideUserText === "string"
        ? options.overrideUserText
        : null;
    const idx = state.conversation.findIndex((m) => m.id === msg.id);
    if (idx <= 0) return;
    const userText = overrideUserText ?? state.conversation[idx - 1]?.text ?? "";
    const previousAttachmentsRaw = Array.isArray(state.conversation[idx - 1]?.attachments)
      ? state.conversation[idx - 1].attachments
      : [];
    const previousAttachments = previousAttachmentsRaw
      .filter((att) => att && (att.url || att.remoteUrl))
      .map((att) => ({
        name: att.name || "attachment",
        type: att.type || "",
        url: toAbsoluteUrl(att.remoteUrl || att.url),
        size: att.size,
        content_hash: att.content_hash || att.contentHash || null,
        origin: att.origin || null,
        relative_path: att.relative_path || att.relativePath || null,
        capture_source: att.capture_source || att.captureSource || null,
        capture_id: att.capture_id || att.captureId || null,
        transient: att.transient === true,
        expires_at: att.expires_at || null,
      }));
    const previousVisionWorkflow =
      state.conversation[idx - 1]?.metadata?.vision?.workflow || "auto";
    if (!userText.trim()) return;
    abortActiveRequest();
    clearActiveRequest();
    setLoading(true);
    setIsStreaming(true);
    setActiveMessageId && setActiveMessageId(msg.id);
    let responseMetadata = null;
    let ragMatchesFromResponse = [];
    let responseThought = "";
    let responseTools = [];
    try {
      let aiResponse = "";
      if (state.backendMode === "api") {
        const controller =
          typeof AbortController !== "undefined" ? new AbortController() : null;
        if (controller) {
          activeRequestRef.current = controller;
        }
        try {
          const res = await apiWrapper.chat(
            {
              message: userText,
              session_id: state.sessionId,
              model: state.apiModel,
              message_id: msg.id,
              attachments: previousAttachments,
              vision_workflow: previousVisionWorkflow,
              ...workflowPayload,
              ...thinkingPayload,
            },
            { signal: controller?.signal },
          );
          if (res?.cancelled) {
            const userCancelError = new Error("Generation cancelled");
            userCancelError.code = "USER_CANCELLED";
            throw userCancelError;
          }
          if (res.error) {
            throw new Error(res.error);
          }
          aiResponse = res.message;
          responseThought = res.thought || "";
          responseTools = Array.isArray(res?.tools_used) ? res.tools_used : [];
          const md = res.metadata || {};
          responseMetadata = Object.keys(md).length ? md : null;
          ragMatchesFromResponse = ragMatchesFromSection(md?.rag);
        } finally {
          if (controller && activeRequestRef.current === controller) {
            clearActiveRequest();
          }
        }
      } else if (state.backendMode === "local" || state.backendMode === "server") {
        const mode = state.backendMode;
        const model = mode === "local" ? (state.localModel || state.transformerModel) : (state.transformerModel || state.apiModel);
        const payload = {
          message: userText,
          mode,
          session_id: state.sessionId,
          message_id: msg.id,
          model,
          attachments: previousAttachments,
          vision_workflow: previousVisionWorkflow,
          ...workflowPayload,
          ...thinkingPayload,
        };
        const r = await requestModelCompletion(payload, userText, {
          trackAbort: true,
          endpoint: "/api/chat",
        });
        aiResponse = r.data?.message || "";
        responseThought = r.data?.thought || "";
        responseTools = Array.isArray(r.data?.tools_used) ? r.data.tools_used : [];
        const meta = r.data?.metadata || {};
        responseMetadata = Object.keys(meta).length ? meta : null;
        ragMatchesFromResponse = ragMatchesFromSection(meta?.rag);
        if (meta?.error || meta?.warning) {
          const actions = [{ label: "Settings", onClick: () => navigate("/settings") }];
          if (mode === "server") {
            actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
          }
          setBanner({
            message: meta.warning || meta.error,
            hint:
              meta.hint ||
              (mode === "server"
                ? "Verify the Server URL and model in Settings (and Harmony formatting for GPT-OSS, e.g. gpt-oss-20b/120b)."
                : "Verify the local model is available and settings are correct."),
            category: meta.category || (mode === "server" ? "server_error" : "local_error"),
            actions,
          });
        } else {
          setBanner(null);
        }
      }

      setState((prev) => {
        const updated = [...prev.conversation];
        if (overrideUserText != null) {
          const userIdx = updated.findIndex((m) => m && m.id === `${msg.id}:user`);
          if (userIdx !== -1) {
            updated[userIdx] = {
              ...updated[userIdx],
              text: overrideUserText,
              timestamp: new Date().toISOString(),
            };
          }
        }
        const mIdx = updated.findIndex((m) => m.id === msg.id);
        if (mIdx !== -1) {
          const entry = {
            ...updated[mIdx],
            text: aiResponse,
            timestamp: new Date().toISOString(),
          };
          if (responseMetadata && Object.keys(responseMetadata).length) {
            entry.metadata = { ...(entry.metadata || {}), ...responseMetadata };
          }
          if (typeof responseThought === "string" && responseThought.trim()) {
            const trimmed = responseThought.trim();
            const thoughts = Array.isArray(entry.thoughts) ? [...entry.thoughts] : [];
            const normalized = normalizeThoughtText(trimmed);
            const merged = mergeThoughtChunks(thoughts);
            const hasThought = merged.some(
              (item) => normalizeThoughtText(item) === normalized,
            );
            if (normalized && !hasThought) thoughts.push(trimmed);
            entry.thoughts = thoughts;
          }
          const mergedTools = mergeToolEntries(
            entry.tools,
            responseTools,
            responseMetadata,
            { includeInlineMetadata: false },
          );
          if (mergedTools.length) {
            entry.tools = mergedTools;
          }
          if (ragMatchesFromResponse.length) {
            entry.ragMatches = ragMatchesFromResponse;
            const ragSection =
              entry.metadata && entry.metadata.rag && typeof entry.metadata.rag === "object"
                ? { ...entry.metadata.rag }
                : {};
            ragSection.matches = ragMatchesFromResponse;
            entry.metadata = { ...(entry.metadata || {}), rag: ragSection };
          }
          updated[mIdx] = entry;
        }
        const hist = [...prev.history];
        if (overrideUserText != null) {
          for (let i = hist.length - 1; i >= 0; i -= 1) {
            if (hist[i]?.role === "user") {
              hist[i] = { role: "user", text: overrideUserText };
              break;
            }
          }
        }
        if (hist.length && hist[hist.length - 1].role === "ai") {
          hist[hist.length - 1] = { role: "ai", text: aiResponse };
        } else if (hist.length && hist[hist.length - 1].role === "user" && aiResponse) {
          hist.push({ role: "ai", text: aiResponse });
        }
        try {
          localStorage.setItem("history", JSON.stringify(hist));
          const payload = JSON.stringify({ sessionId: prev.sessionId, history: hist });
          if (typeof navigator !== "undefined" && navigator.sendBeacon) {
            const blob = new Blob([payload], { type: "application/json" });
            navigator.sendBeacon("/api/history", blob);
          } else {
            axios.post("/api/history", { sessionId: prev.sessionId, history: hist }).catch(() => {});
          }
        } catch {}
        return { ...prev, conversation: updated, history: hist };
      });
    } catch (err) {
      if (isUserCancelledError(err)) {
        setError(null);
        setBanner(null);
        return;
      }
      console.error("Regenerate failed", err);
      const isTimeoutError = err && err.code === "REQUEST_TIMEOUT";
      const detail =
        isTimeoutError
          ? err.message
          : (err && err.response && err.response.data && (err.response.data.detail || err.response.data.message)) || err?.message || "Request failed";
      setError(detail);
      if (isTimeoutError) {
        const actions = [{ label: "Settings", onClick: () => navigate("/settings") }];
        if (state.backendMode === "api") {
          actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
        }
        setBanner({
          message: detail,
          hint: "Generation exceeded the current timeout. Try again or adjust the timeout in Settings.",
          category: "timeout",
          actions,
        });
      } else if (state.backendMode === "api") {
        const actions = [
          { label: "Settings", onClick: () => navigate("/settings") },
          { label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) },
        ];
        setBanner({
          message: detail,
          hint: "Check API key and endpoint or switch to local mode.",
          category: "api_error",
          actions,
        });
      } else {
        setBanner(null);
      }
    } finally {
      setLoading(false);
      setIsStreaming(false);
      setActiveMessageId && setActiveMessageId(null);
      clearActiveRequest();
    }
  };

  const hasInvokedToolResults = useCallback((msg) => {
    if (!msg || typeof msg !== "object") return false;
    const tools = resolveMessageTools(msg);
    return tools.some((t) => {
      if (!t || typeof t !== "object") return false;
      const status = normalizeToolStatus(t.status);
      const hasResult = typeof t.result !== "undefined" && t.result !== null;
      return (
        hasResult ||
        status === "denied" ||
        status === "error" ||
        status === "cancelled" ||
        status === "canceled" ||
        status === "timeout"
      );
    });
  }, []);

  const canContinueMessage = useCallback(
    (msg) => {
      if (!msg || typeof msg !== "object") return false;
      const meta = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
      if (meta.unresolved_tool_loop) return true;
      return hasInvokedToolResults(msg);
    },
    [hasInvokedToolResults],
  );

  const summarizeToolValue = useCallback(
    (value, toolName) => summarizeToolPayloadValue(value, toolName),
    [],
  );

  const continueGenerating = useCallback(
    async (msg, options = {}) => {
      if (!msg || !msg.id) return;
      abortActiveRequest();
      clearActiveRequest();
      setError(null);
      setLoading(true);
      setIsStreaming(true);
      setActiveMessageId && setActiveMessageId(msg.id);
      try {
        const overrideTarget =
          options && typeof options === "object" ? options.continueTarget : null;
        const overrideMode =
          typeof overrideTarget?.mode === "string"
            ? overrideTarget.mode.trim().toLowerCase()
            : "";
        const overrideModel =
          typeof overrideTarget?.model === "string"
            ? overrideTarget.model.trim()
            : "";
        const overrideWorkflow =
          typeof overrideTarget?.workflow === "string"
            ? overrideTarget.workflow.trim()
            : "";
        const continueTarget = resolveModeModel(
          overrideMode || state.backendMode,
          state,
        );
        const resolvedMode =
          overrideMode || continueTarget.mode || state.backendMode;
        const resolvedModel =
          overrideModel || continueTarget.model || state.apiModel;
        const toolPayload = Array.isArray(msg.tools)
          ? msg.tools
              .map(normalizeToolEntry)
              .filter(Boolean)
              .map((tool) => {
                const status = normalizeToolStatus(tool.status);
                const hasResult =
                  typeof tool.result !== "undefined" && tool.result !== null;
                if (hasResult) return tool;
                if (status === "denied") {
                  return {
                    ...tool,
                    result: buildToolOutcomeResult("denied", "Denied by user."),
                  };
                }
                if (status === "error") {
                  return {
                    ...tool,
                    result: buildToolOutcomeResult("error", "Tool error."),
                  };
                }
                if (status === "cancelled" || status === "canceled") {
                  return {
                    ...tool,
                    result: buildToolOutcomeResult("cancelled", "Stopped by user."),
                  };
                }
                if (status === "timeout") {
                  return {
                    ...tool,
                    result: buildToolOutcomeResult("timeout", "Timed out."),
                  };
                }
                return tool;
              })
          : [];
        const toolContinueSignature = buildToolContinuationSignature(toolPayload);
        const semanticToolContinueSignature = buildToolContinuationSignature(
          toolPayload,
          { includeIds: false },
        );
        const res = await axios.post("/api/chat/continue", {
          session_id: state.sessionId,
          message_id: msg.id,
          model: resolvedModel,
          mode: resolvedMode,
          workflow: overrideWorkflow || workflowProfile,
          // Provide fallback results so denials can still unblock continuation.
          tools: toolPayload,
          ...thinkingPayload,
        });
        if (res?.data?.error) {
          throw new Error(res.data.error);
        }
        const aiContinuation = res.data?.message || "";
        const continuationThought = res.data?.thought || "";
        const md = res.data?.metadata || {};
        const returnedTools = Array.isArray(res.data?.tools_used) ? res.data.tools_used : [];
        setState((prev) => {
          const updated = Array.isArray(prev.conversation) ? [...prev.conversation] : [];
          const mIdx = updated.findIndex((m) => m && m.id === msg.id);
          if (mIdx !== -1) {
            const existingText = updated[mIdx]?.text || "";
            const joined = mergeContinuationText(
              existingText,
              aiContinuation,
              updated[mIdx]?.metadata,
            );
            const existingTools = Array.isArray(updated[mIdx]?.tools) ? [...updated[mIdx].tools] : [];
            const mergedTools = [...existingTools];
            returnedTools.forEach((tool) => {
              if (!tool || typeof tool !== "object") return;
              const rawId = tool.id || tool.request_id || null;
              const toolId = rawId ? String(rawId) : null;
              let idx = -1;
              if (toolId) {
                idx = mergedTools.findIndex(
                  (t) =>
                    t &&
                    typeof t === "object" &&
                    (String(t.id || t.request_id || "") === toolId),
                );
              }
              if (idx === -1) {
                const sig = JSON.stringify({ name: tool.name, args: tool.args || {} });
                idx = mergedTools.findIndex(
                  (t) =>
                    t &&
                    typeof t === "object" &&
                    JSON.stringify({ name: t?.name, args: t?.args || {} }) === sig,
                );
              }
              if (idx >= 0) {
                mergedTools[idx] = { ...mergedTools[idx], ...tool };
              } else {
                mergedTools.push(tool);
              }
            });
            const updatedEntry = {
              ...updated[mIdx],
              text: joined,
              timestamp: new Date().toISOString(),
              ...(mergedTools.length ? { tools: mergedTools } : {}),
              metadata: {
                ...(updated[mIdx]?.metadata || {}),
                ...(md || {}),
                tool_continued: true,
                ...(toolContinueSignature && !md?.tool_continue_signature
                  ? { tool_continue_signature: toolContinueSignature }
                  : {}),
                ...(semanticToolContinueSignature &&
                !md?.tool_continue_semantic_signature
                  ? {
                      tool_continue_semantic_signature:
                        semanticToolContinueSignature,
                    }
                  : {}),
              },
            };
            if (typeof continuationThought === "string" && continuationThought.trim()) {
              const trimmed = continuationThought.trim();
              const thoughts = Array.isArray(updatedEntry.thoughts)
                ? [...updatedEntry.thoughts]
                : [];
              const normalized = normalizeThoughtText(trimmed);
              const merged = mergeThoughtChunks(thoughts);
              const hasThought = merged.some(
                (item) => normalizeThoughtText(item) === normalized,
              );
              if (normalized && !hasThought) thoughts.push(trimmed);
              updatedEntry.thoughts = thoughts;
            }
            updated[mIdx] = updatedEntry;
          }
          const hist = Array.isArray(prev.history) ? [...prev.history] : [];
          if (hist.length && hist[hist.length - 1].role === "ai") {
            const last = hist[hist.length - 1].text || "";
            hist[hist.length - 1] = {
              role: "ai",
              text: mergeContinuationText(last, aiContinuation),
            };
          } else if (aiContinuation) {
            hist.push({ role: "ai", text: aiContinuation });
          }
          try {
            localStorage.setItem("history", JSON.stringify(hist));
            const payload = JSON.stringify({ sessionId: prev.sessionId, history: hist });
            if (typeof navigator !== "undefined" && navigator.sendBeacon) {
              const blob = new Blob([payload], { type: "application/json" });
              navigator.sendBeacon("/api/history", blob);
            } else {
              axios.post("/api/history", { sessionId: prev.sessionId, history: hist }).catch(() => {});
            }
          } catch {}
          return { ...prev, conversation: updated, history: hist };
        });
      } catch (err) {
        const detail =
          (err && err.response && err.response.data && (err.response.data.detail || err.response.data.message)) ||
          err?.message ||
          "Continue failed";
        setError(detail);
      } finally {
      setLoading(false);
      setIsStreaming(false);
      setActiveMessageId && setActiveMessageId(null);
    }
  },
  [
    abortActiveRequest,
    buildToolOutcomeResult,
    clearActiveRequest,
    setActiveMessageId,
    setState,
    state.apiModel,
    state.backendMode,
    state.localModel,
    state.sessionId,
    state.transformerModel,
  ],
  );

  const maybeContinueAfterTools = useCallback(
    async (msgBase, toolsOverride = null, continueTarget = null) => {
      if (!msgBase || !msgBase.id) return;
      if (toolContinueLocksRef.current.has(msgBase.id)) return;
      const tools = Array.isArray(toolsOverride) ? toolsOverride : msgBase.tools;
      const batch = buildToolContinuationBatch(tools);
      if (!batch) return;
      if (
        hasMatchingToolContinuationSignature(msgBase.metadata, batch) ||
        hasMatchingToolContinuationSignature(msgBase.metadata, batch, {
          includeIds: false,
        })
      ) {
        return;
      }
      toolContinueLocksRef.current.add(msgBase.id);
      try {
        await continueGenerating(
          { ...msgBase, tools: batch },
          continueTarget ? { continueTarget } : undefined,
        );
      } finally {
        toolContinueLocksRef.current.delete(msgBase.id);
      }
    },
    [continueGenerating],
  );

  const openEditUserMessage = useCallback(
    (userMsg) => {
      if (!userMsg || !userMsg.id) return;
      const rawId = String(userMsg.id);
      const baseId = rawId.endsWith(":user") ? rawId.slice(0, -5) : null;
      if (!baseId) return;
      const assistantMsg = state.conversation.find((m) => m && m.id === baseId);
      if (!assistantMsg) {
        const timestampIso = new Date().toISOString();
        setState((prev) => {
          const updated = Array.isArray(prev.conversation) ? [...prev.conversation] : [];
          const userIdx = updated.findIndex((m) => m && m.id === rawId);
          if (userIdx === -1) return prev;
          if (updated.some((m) => m && m.id === baseId)) return prev;
          const placeholder = {
            role: "ai",
            id: baseId,
            text: "",
            thoughts: [],
            tools: [],
            timestamp: timestampIso,
            metadata: { status: "pending" },
          };
          updated.splice(userIdx + 1, 0, placeholder);
          return { ...prev, conversation: updated };
        });
      }
      setMessageEditorState({
        mode: "user",
        assistantId: baseId,
        text: typeof userMsg.text === "string" ? userMsg.text : "",
      });
    },
    [setState, state.conversation],
  );

  const openEditAssistantMessage = useCallback((assistantMsg) => {
    if (!assistantMsg || !assistantMsg.id) return;
    setMessageEditorState({
      mode: "assistant",
      assistantId: assistantMsg.id,
      text: typeof assistantMsg.text === "string" ? assistantMsg.text : "",
    });
  }, []);

  const applyAssistantEdit = useCallback(
    (assistantId, nextText) => {
      if (!assistantId) return;
      const cleaned = typeof nextText === "string" ? nextText : "";
      setState((prev) => {
        const updatedConversation = Array.isArray(prev.conversation) ? [...prev.conversation] : [];
        const idx = updatedConversation.findIndex((m) => m && m.id === assistantId);
        if (idx === -1) return prev;
        updatedConversation[idx] = {
          ...updatedConversation[idx],
          text: cleaned,
          timestamp: new Date().toISOString(),
          metadata: { ...(updatedConversation[idx]?.metadata || {}), edited: true },
        };
        const hist = updatedConversation
          .filter((m) => m && (m.role === "user" || m.role === "ai") && typeof m.text === "string")
          .map((m) => ({ role: m.role, text: m.text }));
        try {
          localStorage.setItem("conversation", JSON.stringify(updatedConversation));
          localStorage.setItem("history", JSON.stringify(hist));
          const payload = JSON.stringify({ sessionId: prev.sessionId, history: hist });
          if (typeof navigator !== "undefined" && navigator.sendBeacon) {
            const blob = new Blob([payload], { type: "application/json" });
            navigator.sendBeacon("/api/history", blob);
          } else {
            axios.post("/api/history", { sessionId: prev.sessionId, history: hist }).catch(() => {});
          }
        } catch {}
        return { ...prev, conversation: updatedConversation, history: hist };
      });
    },
    [setState],
  );

  const handleInlineToolClick = useCallback(
    (event) => {
      const target = event?.target;
      const el =
        target && target instanceof Element
          ? target.closest(".inline-tool-placeholder")
          : null;
      if (!el) return;
      event.preventDefault();
      const toolId = el.getAttribute("data-tool-id") || null;
      const chainId = el.getAttribute("data-chain-id") || null;
      const wantsInline = toolLinkBehavior === "inline";
      const canShowInline = wantsInline && inlineToolsEnabled && chainId;
      if (canShowInline) {
        setActiveMessageId && setActiveMessageId(chainId);
        setCollapsedTools((prev) => ({
          ...prev,
          [chainId]: false,
        }));
        return;
      }
      if ((!canShowInline || !inlineToolsEnabled) && typeof onOpenConsole === "function") {
        onOpenConsole({
          toolId,
          chainId,
        });
      }
    },
    [inlineToolsEnabled, onOpenConsole, setActiveMessageId, toolLinkBehavior],
  );

  const openBrowserSessionInspector = useCallback((computer) => {
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

  const invokeBrowserSessionTool = useCallback(
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
          activeBrowserSession.sessionKey || state.sessionId || activeBrowserSession.sessionId,
        message_id: activeBrowserSession.messageId || undefined,
        chain_id: activeBrowserSession.chainId || undefined,
      };
      const resp = await axios.post("/api/tools/invoke", payload);
      const result = resp?.data?.result;
      const computer = extractComputerPayload(result, toolName);
      if (computer?.sessionId) {
        setBrowserSessionOverrides((prev) => ({
          ...prev,
          [computer.sessionId]: {
            ...prev[computer.sessionId],
            ...activeBrowserSession,
            ...computer,
            attachment: computer.attachment || activeBrowserSession?.attachment || null,
            summary: computer.summary || activeBrowserSession?.summary || "",
            order: Date.now(),
          },
        }));
      }
      return result;
    },
    [activeBrowserSession, state.sessionId],
  );

  const runBrowserSessionAction = useCallback(async (actionLabel, callback) => {
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
  }, []);

  const handleBrowserPopupObserve = useCallback(() => {
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

  const handleBrowserPopupNavigate = useCallback(
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

  const handleBrowserPopupType = useCallback(
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

  const handleBrowserPopupKeypress = useCallback(
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

  const handleBrowserPreviewClick = useCallback(
    (event) => {
      if (!activeBrowserSession?.sessionId || browserPopupPendingAction) return;
      const img = event.currentTarget;
      const rect = img.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const width = img.naturalWidth || activeBrowserSession.session?.width || 0;
      const height = img.naturalHeight || activeBrowserSession.session?.height || 0;
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

  const handleInlineToolKeyDown = useCallback(
    (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const target = event?.target;
      const el =
        target && target instanceof Element
          ? target.closest(".inline-tool-placeholder")
          : null;
      if (!el) return;
      event.preventDefault();
      handleInlineToolClick(event);
    },
    [handleInlineToolClick],
  );

  const renderContent = (msg, idx, toolsForRender = null) => {
    const rawText =
      typeof msg?.text === "string"
        ? msg.text
        : typeof msg?.content === "string"
          ? msg.content
          : "";
    if (typeof rawText !== "string") return rawText;
    const text =
      (msg?.role === "ai" || msg?.role === "assistant") && rawText.includes("<|")
        ? stripHarmonyEnvelope(rawText)
        : rawText;
    const renderMath = (input) => {
      if (!window.katex || typeof window.katex.renderToString !== "function") return input;
      let output = input;
      const patterns = [
        { re: /\$\$([\s\S]+?)\$\$/g, display: true },
        { re: /\\\[([\s\S]+?)\\\]/g, display: true },
        { re: /\\\(([\s\S]+?)\\\)/g, display: false },
        { re: /\$([^\n$]+?)\$/g, display: false },
      ];
      patterns.forEach(({ re, display }) => {
        output = output.replace(re, (_, expr) => {
          try {
            return window.katex.renderToString(expr.trim(), {
              throwOnError: false,
              displayMode: display,
            });
          } catch (err) {
            console.error("KaTeX render error", err);
            return _;
          }
        });
      });
      return output;
    };
    if (/```mermaid/.test(text)) {
      const code = text.replace(/```mermaid|```/g, "").trim();
      const id = `merm-${idx}`;
      setTimeout(() => {
        if (window.mermaid) {
          window.mermaid.render(id, code, (svg) => {
            document.getElementById(id).innerHTML = svg;
          });
        }
      }, 0);
      return <div id={id} />;
    }
    if (/\.(png|jpg|jpeg|gif|svg|mp4|webm|mp3|wav)$/i.test(text)) {
      return <MediaViewer src={text} />;
    }
    const tools = Array.isArray(toolsForRender)
      ? toolsForRender
      : Array.isArray(msg?.tools)
        ? msg.tools
        : [];
    const inlinePayloads = extractInlineToolPayloads(msg?.metadata);
    const chainId = msg?.id || msg?.message_id || null;
    const getToolEntry = (toolIndex) => {
      const rawPayload = inlinePayloads[toolIndex];
      const parsed = parseInlineToolPayload(rawPayload);
      if (parsed) {
        const signature = JSON.stringify({ name: parsed.name, args: parsed.args || {} });
        const matched =
          tools.find(
            (tool) =>
              tool &&
              JSON.stringify({ name: tool?.name, args: tool?.args || {} }) === signature,
          ) || tools.find((tool) => tool && tool.name === parsed.name);
        if (matched) return matched;
      }
      if (Number.isInteger(toolIndex) && tools[toolIndex]) {
        return tools[toolIndex];
      }
      return null;
    };
    const withPlaceholders = text.replace(TOOL_PLACEHOLDER_RE, (match, rawIndex) => {
      const toolIndex = Number.parseInt(rawIndex, 10);
      const entry = getToolEntry(toolIndex);
      const rawPayload = inlinePayloads[toolIndex];
      const parsed = parseInlineToolPayload(rawPayload);
      const toolName =
        (entry && entry.name) || (parsed && parsed.name) || "tool call";
      const label = toolName;
      const toolId = entry?.id || entry?.request_id || null;
      const attrs = [
        `href=\"#\"`,
        `data-tool-index=\"${toolIndex}\"`,
        chainId ? `data-chain-id=\"${escapeHtml(chainId)}\"` : "",
        toolId ? `data-tool-id=\"${escapeHtml(toolId)}\"` : "",
        `aria-label=\"Open ${escapeHtml(label)}\"`,
      ]
        .filter(Boolean)
        .join(" ");
      return `<a class=\"inline-tool-placeholder\" ${attrs}>${escapeHtml(label)}</a>`;
    });
    const maybeMath = renderMath(withPlaceholders);
    try {
      const html = DOMPurify.sanitize(
        marked.parse(maybeMath, {
          breaks: true,
          gfm: true,
        }),
        {
          ADD_ATTR: [
            "data-tool-id",
            "data-tool-index",
            "data-chain-id",
            "href",
            "aria-label",
          ],
        },
      );
      return (
        <div
          className="markdown-body"
          dangerouslySetInnerHTML={{ __html: html }}
          onClick={handleInlineToolClick}
          onKeyDown={handleInlineToolKeyDown}
        />
      );
    } catch (err) {
      console.error("Markdown render error", err);
    }
    return text;
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleFileChange = (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    files.forEach((file) => uploadAndAttach(file));
    // reset the input so selecting the same file again triggers change
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const uploadAndAttach = async (file, options = {}) => {
    // basic client-side checks mirroring backend
    const max = 8 * 1024 * 1024;
    if (file.size > max) {
      setError("File too large (max 8MB)");
      return;
    }
    const allowed = [
      "text/plain",
      "application/pdf",
      "image/png",
      "image/jpeg",
      "image/gif",
      "image/webp",
      "audio/mpeg",
      "audio/wav",
      "video/mp4",
      "video/webm",
    ];
    if (!allowed.includes(file.type)) {
      setError("Unsupported file type");
      return;
    }
    const id = crypto.randomUUID();
    const url = URL.createObjectURL(file);
    const origin = options.origin || "upload";
    const captureSource = options.captureSource || null;
    const isTransientCapture = origin === "captured";
    setAttachments((prev) => [
      ...prev,
      {
        id,
        file,
        url,
        remoteUrl: null,
        uploading: true,
        contentHash: null,
        origin,
        capture_source: captureSource,
        transient: isTransientCapture,
      },
    ]);
    try {
      const formData = new FormData();
      formData.append("file", file);
      let res;
      if (isTransientCapture) {
        const captureKind =
          captureSource && String(captureSource).toLowerCase().includes("screen")
            ? "screen"
            : "camera";
        formData.append("source", captureKind);
        res = await axios.post("/api/captures/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else {
        formData.append("origin", origin);
        if (captureSource) {
          formData.append("capture_source", captureSource);
        }
        res = await axios.post("/api/attachments/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }
      const remoteUrl = res.data?.url;
      const contentHash = res.data?.content_hash || null;
      const captureId = res.data?.capture_id || null;
      setAttachments((prev) =>
        prev.map((a) =>
          a.id === id
            ? {
                ...a,
                remoteUrl,
                contentHash,
                uploading: false,
                origin: res.data?.origin || origin,
                relative_path: res.data?.relative_path || "",
                capture_source: captureSource,
                capture_id: captureId,
                transient:
                  typeof res.data?.transient === "boolean"
                    ? res.data.transient
                    : isTransientCapture,
                promoted: res.data?.promoted === true,
                expires_at: res.data?.expires_at_iso || res.data?.expires_at || null,
                index_status: res.data?.index_status || a.index_status || null,
                caption_status: res.data?.caption_status || a.caption_status || null,
              }
            : a,
        ),
      );
      if (!isTransientCapture) {
        // best-effort: record durable attachments in memory for future recall
        try {
          await axios.post("/api/memory/update/", {
            key: "attachment",
            value: {
              name: file.name,
              type: file.type,
              size: file.size.toString(),
              url: remoteUrl,
            },
          });
        } catch (_) { /* non-fatal */ }
      }
    } catch (err) {
      console.error("Attachment upload failed", err);
      setError(getRequestErrorDetail(err, "Attachment upload failed"));
      setAttachments((prev) => prev.map((a) => (
        a.id === id ? { ...a, uploading: false } : a
      )));
    }
  };

  const handleComposerPaste = (event) => {
    const clipboardData = event?.clipboardData;
    const items = Array.isArray(clipboardData?.items)
      ? clipboardData.items
      : Array.from(clipboardData?.items || []);
    if (!items.length) return;

    const imageFiles = items
      .filter((item) => item?.kind === "file" && String(item.type || "").startsWith("image/"))
      .map((item, index) => {
        const file = item.getAsFile?.();
        if (!(file instanceof File)) return null;
        if (file.name) return file;
        const extension = file.type === "image/jpeg" ? "jpg" : "png";
        return new File([file], `pasted-image-${Date.now()}-${index + 1}.${extension}`, {
          type: file.type || "image/png",
        });
      })
      .filter(Boolean);

    if (!imageFiles.length) return;

    imageFiles.forEach((file) => uploadAndAttach(file));

    const pastedText =
      typeof clipboardData?.getData === "function" ? clipboardData.getData("text/plain") : "";
    if (!String(pastedText || "").trim()) {
      event.preventDefault();
    }
  };

  const captureCameraFrame = async () => {
    const video = cameraVideoRef.current;
    if (!video) {
      setCameraError("Camera preview is unavailable.");
      return;
    }
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      setCameraError("Could not access camera frame buffer.");
      return;
    }
    ctx.drawImage(video, 0, 0, width, height);
    const blob = await new Promise((resolve) => {
      canvas.toBlob(resolve, "image/png");
    });
    if (!(blob instanceof Blob)) {
      setCameraError("Camera capture failed.");
      return;
    }
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const capturedFile = new File([blob], `camera-${stamp}.png`, {
      type: "image/png",
    });
    await uploadAndAttach(capturedFile, {
      origin: "captured",
      captureSource: "chat_camera",
    });
    stopCameraCapture();
  };

  const removeAttachment = (id) => {
    setAttachments((prev) => {
      const found = prev.find((a) => a.id === id);
      if (found) revokeAttachmentPreview(found);
      return prev.filter((a) => a.id !== id);
    });
  };

  const handleAudioStop = async () => {
    const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("file", blob, "recording.webm");
    try {
      const res = await axios.post("/api/voice/stream", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const taskId = res.data.task_id;
      const poll = async () => {
        try {
          const taskRes = await axios.get(`/api/tasks/${taskId}`);
          if (taskRes.data.state === "SUCCESS") {
            const { text } = taskRes.data.result;
            await sendMessage(text);
          } else if (taskRes.data.state === "PENDING") {
            setTimeout(poll, 1000);
          } else {
            setError("Audio processing failed");
          }
        } catch (err) {
          console.error("Task polling failed", err);
          setError("Audio processing failed");
        }
      };
      poll();
    } catch (err) {
      console.error("Audio upload failed", err);
      setError("Audio upload failed");
    }
  };

  const toggleAudioRecording = async () => {
    if (audioRecording) {
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
        mediaRecorderRef.current.stream.getTracks().forEach((t) => t.stop());
      }
      setAudioRecording(false);
      return;
    }
    if (state.backendMode === "api" && state.apiStatus !== "online") {
      setError("API not ready");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: buildAudioConstraints(),
      });
      const mediaRecorder = new MediaRecorder(stream);
      audioChunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = handleAudioStop;
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setAudioRecording(true);
    } catch (err) {
      console.error("Audio record failed", err);
      setError("Audio record failed");
    }
  };

  const toggleRecording = async () => {
    if (recording || liveSessionPending || liveStreamingPhase === "connecting") {
      stopLiveVoiceSession();
      return;
    }
    const attemptId = liveSessionAttemptRef.current + 1;
    liveSessionAttemptRef.current = attemptId;
    stopMicTest();
    setChatSettingsOpen(false);
    if (state.backendMode === "api" && state.apiStatus !== "online") {
      setError("API not ready");
      return;
    }
    if (state.backendMode !== "api") {
      setError("Live streaming mode requires API backend");
      return;
    }
    try {
      setError(null);
      setLiveSessionPending(true);
      setLiveStreamingPhase("connecting");
      setLiveStreamingTranscript({ user: "", assistant: "" });
      const res = await axios.post("/api/voice/connect", {
        identity:
          typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
            ? crypto.randomUUID()
            : `voice-${Date.now()}`,
        room: "float",
      });
      ensureLiveSessionAttemptCurrent(attemptId);
      const session = res?.data || {};
      if (
        session.provider === "openai-realtime" ||
        typeof session.client_secret === "string"
      ) {
        await startOpenAiRealtimeVoice(session, attemptId);
        ensureLiveSessionAttemptCurrent(attemptId);
        if (liveCameraDefaultEnabled) {
          await enableLiveCamera().catch((err) => {
            console.error("default live camera failed", err);
            setLiveVisualError(getRequestErrorDetail(err, "Camera access failed."));
          });
        }
        return;
      }
      await startLiveKitVoice(session, attemptId);
      ensureLiveSessionAttemptCurrent(attemptId);
      if (liveCameraDefaultEnabled) {
        await enableLiveCamera().catch((err) => {
          console.error("default live camera failed", err);
          setLiveVisualError(getRequestErrorDetail(err, "Camera access failed."));
        });
      }
    } catch (err) {
      if (isLiveSessionCancelledError(err)) {
        return;
      }
      console.error("voice connect failed", err);
      stopLiveVoiceSession();
      const detail = getRequestErrorDetail(
        err,
        "Live streaming mode failed to start.",
      );
      setError(detail);
      setBanner({
        message: "Live streaming mode failed",
        hint: detail,
        category: "warning",
      });
    }
  };

  useEffect(() => {
    return () => {
      stopLiveVoiceSession();
    };
  }, [stopLiveVoiceSession]);

  useEffect(() => {
    return () => {
      stopTtsPlayback();
    };
  }, [stopTtsPlayback]);

  const scrollToBottomButton = !isAtBottom ? (
    <Tooltip title="Scroll to latest message" placement="top">
      <button
        type="button"
        className="scroll-to-bottom-btn"
        onClick={() => scheduleScrollToBottom("smooth")}
        aria-label="Scroll to latest message"
      >
        &#8595;
      </button>
    </Tooltip>
  ) : null;

  const hasUploadingAttachments = attachments.some((att) => Boolean(att?.uploading));
  const hasDraftText = Boolean(message && message.trim());
  const hasSendableAttachments = attachments.length > 0;
  const compareNeedsMoreImages =
    hasImageAttachments && visionWorkflow === "compare" && imageAttachmentCount < 2;
  const sendDisabled = isStreaming
    ? false
    : loading || (!hasDraftText && !hasSendableAttachments) || hasUploadingAttachments || compareNeedsMoreImages;
  const sendTooltip = isStreaming
    ? "Stop generation"
    : hasUploadingAttachments
      ? "Attachments are still uploading"
      : compareNeedsMoreImages
        ? "Compare mode needs at least two images"
        : !hasDraftText && hasSendableAttachments
          ? "Send attachments"
      : hasDraftText
        ? "Send message"
        : "Type a message to send";
  const liveStreamingStatusLabel = getLiveStreamingStatusLabel(
    liveStreamingPhase,
  );
  const liveStreamingActive =
    recording || liveSessionPending || liveStreamingPhase === "connecting";
  const liveTranscriptVisible =
    liveStreamingActive &&
    state.liveTranscriptEnabled !== false &&
    (liveStreamingPhase !== "idle" ||
      Boolean(liveStreamingTranscript.user?.trim()) ||
      Boolean(liveStreamingTranscript.assistant?.trim()));
  const liveStreamingIndicator =
    liveStreamingActive && typeof document !== "undefined"
      ? createPortal(
          <div
            className={`live-streaming-indicator live-streaming-indicator--${liveStreamingPhase}`}
            aria-live="polite"
          >
            <span className="live-streaming-indicator-pulse" aria-hidden="true" />
            <div className="live-streaming-indicator-copy">
              <strong>live streaming mode</strong>
              <span>{liveStreamingStatusLabel}</span>
            </div>
          </div>,
          document.body,
        )
      : null;
  const chatSettingsPopover =
    chatSettingsOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={chatSettingsPopoverRef}
            className="chat-settings-popover"
            role="menu"
            style={chatSettingsPopoverStyle || { visibility: "hidden" }}
          >
            <div className="chat-settings-list">
              {[
                ["camera", "camera"],
                ["mic", "mic"],
                ["volume", "volume"],
                ["thinking", "thinking"],
                ["workflow", "workflow"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`chat-settings-item${
                    chatSettingsSection === key ? " is-active" : ""
                  }`}
                  onMouseEnter={() => setChatSettingsSection(key)}
                  onFocus={() => setChatSettingsSection(key)}
                  onClick={() => setChatSettingsSection(key)}
                >
                  <span>{label}</span>
                  <KeyboardArrowRightIcon fontSize="inherit" />
                </button>
              ))}
            </div>
            <div
              className="chat-settings-panel"
              style={{
                marginTop: `${LIVE_TOOL_PANEL_OFFSETS[chatSettingsSection] || 0}px`,
              }}
            >
              {chatSettingsSection === "camera" && (
                <>
                  <label htmlFor="chat-camera-input">camera input</label>
                  <select
                    id="chat-camera-input"
                    value={preferredCameraDeviceId}
                    onChange={(event) =>
                      setState((prev) => ({
                        ...prev,
                        preferredCameraDeviceId: event.target.value,
                      }))
                    }
                  >
                    <option value="">default camera</option>
                    {availableInputDevices.videoinput.map((device) => (
                      <option key={device.deviceId} value={device.deviceId}>
                        {device.label}
                      </option>
                    ))}
                  </select>
                  <div className="chat-settings-inline">
                    <button
                      type="button"
                      className="chip"
                      onClick={toggleLiveCamera}
                      disabled={cameraBusy}
                    >
                      {recording
                        ? liveVisualMode === "camera"
                          ? "camera off"
                          : "camera on"
                        : cameraOpen
                          ? "stop preview"
                          : "preview camera"}
                    </button>
                  </div>
                  <label className="chat-settings-checkbox">
                    <input
                      type="checkbox"
                      checked={liveCameraDefaultEnabled}
                      onChange={(event) =>
                        setState((prev) => ({
                          ...prev,
                          liveCameraDefaultEnabled: event.target.checked,
                        }))
                      }
                    />
                    <span>start camera when live streaming starts</span>
                  </label>
                </>
              )}
              {chatSettingsSection === "mic" && (
                <>
                  <label htmlFor="chat-mic-input">mic input</label>
                  <select
                    id="chat-mic-input"
                    value={preferredMicDeviceId}
                    onChange={(event) =>
                      setState((prev) => ({
                        ...prev,
                        preferredMicDeviceId: event.target.value,
                      }))
                    }
                  >
                    <option value="">default microphone</option>
                    {availableInputDevices.audioinput.map((device) => (
                      <option key={device.deviceId} value={device.deviceId}>
                        {device.label}
                      </option>
                    ))}
                  </select>
                  <div className="chat-settings-inline">
                    <button
                      type="button"
                      className="chip"
                      onClick={toggleMicTest}
                      disabled={recording}
                    >
                      {micTestActive ? "stop test" : "test mic"}
                    </button>
                    {recording && (
                      <span className="chat-settings-note">live session owns mic input</span>
                    )}
                  </div>
                  <div className="chat-settings-meter" aria-hidden="true">
                    <span
                      className="chat-settings-meter-fill"
                      style={{ width: `${Math.round(micTestLevel * 100)}%` }}
                    />
                  </div>
                </>
              )}
              {chatSettingsSection === "volume" && (
                <>
                  <label htmlFor="chat-mic-gain">mic level</label>
                  <input
                    id="chat-mic-gain"
                    type="range"
                    min="25"
                    max="200"
                    step="5"
                    value={Math.round(micInputGain * 100)}
                    onChange={(event) =>
                      setState((prev) => ({
                        ...prev,
                        micInputGain: Number(event.target.value) / 100,
                      }))
                    }
                  />
                  <span className="chat-settings-slider-value">
                    {Math.round(micInputGain * 100)}%
                  </span>
                  <label htmlFor="chat-output-volume">speaker level</label>
                  <input
                    id="chat-output-volume"
                    type="range"
                    min="0"
                    max="150"
                    step="5"
                    value={Math.round(outputVolume * 100)}
                    onChange={(event) =>
                      setState((prev) => ({
                        ...prev,
                        outputVolume: Number(event.target.value) / 100,
                      }))
                    }
                  />
                  <span className="chat-settings-slider-value">
                    {Math.round(outputVolume * 100)}%
                  </span>
                </>
              )}
              {chatSettingsSection === "thinking" && (
                <>
                  <label>thinking mode</label>
                  <div className="chat-settings-choice-row">
                    {["auto", "low", "high"].map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        className={`chat-settings-choice${
                          thinkingMode === mode ? " is-active" : ""
                        }`}
                        onClick={() => setThinkingMode(mode)}
                      >
                        {mode}
                      </button>
                    ))}
                  </div>
                </>
              )}
              {chatSettingsSection === "workflow" && (
                <>
                  <label>workflow profile</label>
                  <div className="chat-settings-choice-row">
                    {[
                      ["default", "default"],
                      ["architect_planner", "architect"],
                      ["mini_execution", "mini"],
                    ].map(([workflow, label]) => (
                      <button
                        key={workflow}
                        type="button"
                        className={`chat-settings-choice${
                          workflowProfile === workflow ? " is-active" : ""
                        }`}
                        onClick={() => setWorkflowProfile(workflow)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <span className="chat-settings-note">
                    Default balances quality. Architect plans more. Mini is for short execution bursts.
                  </span>
                </>
              )}
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
    <div className="chat-container">
      {state.backendMode === "api" && state.apiStatus !== "online" && (
        <div className="api-warning">
          {state.apiStatus === "loading" ? "Loading..." : "Unable to reach API"}
        </div>
      )}
      {hasAnyTools && (
        <div className="chat-tools-toolbar">
          <button
            type="button"
            className="tool-collapse-btn tool-collapse-all-btn"
            onClick={toggleCollapseAllTools}
          >
            {collapseAllTools ? "expand all tools" : "collapse all tools"}
          </button>
        </div>
      )}
      {liveStreamingIndicator}
      <div className="chat-box" ref={chatBoxRef}>
        {state.conversation.length === 0 && (
          <p className="placeholder">Start chatting!</p>
        )}
        {state.conversation.map((msg, idx) => {
          const ragMatches = getMessageRagMatches(msg);
          const fragmentKey = msg && msg.id ? msg.id : idx;
          const previousTimestamp = idx > 0 ? state.conversation[idx - 1]?.timestamp : null;
          const timestampLabel = msg?.timestamp
            ? formatMessageTimestampLabel(msg.timestamp, previousTimestamp)
            : "";
          const timestampTitle = msg?.timestamp ? formatMessageTimestampTitle(msg.timestamp) : "";
          const isActiveMessage = msg && msg.id && msg.id === activeMessageId;
          const thoughtBlocks = isActiveMessage ? buildThoughtBlocks(msg.thoughts) : [];
          const resolvedTools = resolveMessageTools(msg);
          const messageSourceLabel =
            msg && (msg.role === "ai" || msg.role === "assistant") ? getMessageSourceLabel(msg) : "";
          const messageStatusBadge =
            msg && (msg.role === "ai" || msg.role === "assistant")
              ? getMessageStatusBadge(msg)
              : null;
          return (
            <React.Fragment key={fragmentKey}>
              <div
              ref={(el) => {
                if (msg && msg.id) {
                  messageRefs.current[msg.id] = el;
                }
              }}
              onClick={(event) => {
                event.stopPropagation();
                setActiveMessageId && msg?.id && setActiveMessageId(msg.id);
              }}
              className={`${
                msg.role === "user" ? "user-msg" : "ai-msg"
              } ${highlightChainId === msg.id ? "chain-highlight" : ""} ${
                activeMessageId === msg.id ? "selected" : ""
              }`}
            >
              {thoughtBlocks.length > 0 && (
                <div className="inline-thought-block">
                  {thoughtBlocks.map((t, i) => (
                    <div key={`t-${i}`} className="inline-thought">
                      {t}
                    </div>
                  ))}
                </div>
              )}
              {renderContent(msg, idx, resolvedTools)}
              {(() => {
                if (!Array.isArray(msg.attachments) || !msg.attachments.length) return null;
                const attachmentsList = msg.attachments;
                const mediaEntries = [];
                attachmentsList.forEach((att, index) => {
                  const candidateSrc = att.url || att.src;
                  if (!candidateSrc) return;
                  const candidateType = (att.type || "").toLowerCase();
                  const candidateName = att.name || `attachment-${index + 1}`;
                  const looksMedia =
                    candidateType.startsWith("image/") ||
                    candidateType.startsWith("video/") ||
                    candidateType.startsWith("audio/") ||
                    /\\.(png|jpe?g|gif|svg|webp|mp4|webm|mp3|wav|pdf)$/i.test(candidateSrc);
                  if (looksMedia) {
                    mediaEntries.push({
                      index,
                      item: {
                        src: candidateSrc,
                        alt: candidateName,
                        file: att.file || null,
                        label: candidateName,
                        size:
                          typeof att.size === "number"
                            ? att.size
                            : typeof att.file?.size === "number"
                              ? att.file.size
                              : null,
                        uploadedAt: att.uploaded_at || att.created_at || null,
                        contentHash: att.content_hash || att.contentHash || null,
                        origin: att.origin || null,
                        relative_path: att.relative_path || att.relativePath || null,
                        capture_source: att.capture_source || att.captureSource || null,
                        caption_status: att.caption_status || null,
                        index_status: att.index_status || null,
                        placeholder_caption: att.placeholder_caption ?? null,
                      },
                    });
                  }
                });
                const mediaContextItems = mediaEntries.map((entry) => entry.item);
                const mediaIndexByAttachment = new Map(
                  mediaEntries.map((entry, position) => [entry.index, position])
                );
                return (
                  <div className="message-attachments">
                    {attachmentsList.map((att, i) => {
                      const src = att.url || att.src;
                      const name = att.name || `attachment-${i + 1}`;
                      const t = (att.type || "").toLowerCase();
                      const isMedia =
                        t.startsWith("image/") ||
                        t.startsWith("video/") ||
                        t.startsWith("audio/") ||
                        /\\.(png|jpe?g|gif|svg|webp|mp4|webm|mp3|wav|pdf)$/i.test(src || "");
                      const mediaIndex = mediaIndexByAttachment.get(i);
                      return (
                        <div key={`att-${i}`} className="message-attachment">
                          {isMedia && src ? (
                            <MediaViewer
                              src={src}
                              alt={name}
                              file={att.file || null}
                              contextItems={mediaContextItems}
                              contextIndex={typeof mediaIndex === "number" ? mediaIndex : 0}
                            />
                          ) : src ? (
                            <a
                              href={src}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="attachment-link"
                              title={name}
                            >
                              {name}
                            </a>
                          ) : (
                            <span className="attachment-missing">{name}</span>
                          )}
                        </div>
                      );
                    })}
              </div>
            );
          })()}
          <RagContextPanel
            matches={ragMatches}
            defaultOpen={false}
          />
              {toolChainIds.has(msg.id) && (
                <span
                  className="chain-overlay"
                  onMouseEnter={() => setHoverChainId(msg.id)}
                  onMouseLeave={() => setHoverChainId(null)}
                  onClick={() =>
                    setActiveChainId((prev) =>
                      prev === msg.id ? null : msg.id
                    )
                  }
                />
              )}
              {shouldShowInlineToolsForMessage(msg, idx) && resolvedTools.length > 0 && (() => {
                const toolCollapseKey = msg.id || msg.message_id || fragmentKey;
                const toolsCollapsed = Object.prototype.hasOwnProperty.call(
                  collapsedTools,
                  toolCollapseKey,
                )
                  ? collapsedTools[toolCollapseKey]
                  : collapseAllTools;
                const toolCount = resolvedTools.length;
                return (
                  <div className={`inline-tool-list${isActiveMessage ? " active" : ""}`}>
                    <div className="inline-tool-toolbar">
                      <button
                        type="button"
                        className="tool-collapse-btn"
                        onClick={() => toggleToolCollapse(toolCollapseKey)}
                      >
                        {toolsCollapsed
                          ? `show tools (${toolCount})`
                          : "hide tools"}
                      </button>
                    </div>
                    {!toolsCollapsed && resolvedTools.map((tool, i) => {
                    const statusRaw =
                      typeof tool.status === "string" && tool.status.trim()
                        ? tool.status.trim()
                        : "";
                    const status =
                      getEffectiveToolStatus(tool) ||
                      normalizeToolStatus(statusRaw || "proposed");
                    const isPending = status === "proposed" || status === "pending";
                    const statusDisplay = getToolStatusDisplay(status, statusRaw);
                    const statusTone = statusDisplay.tone;
                    const statusLabel = statusDisplay.label;
                    const statusGlyph = statusDisplay.glyph;
                    const hasArgs =
                      tool.args && typeof tool.args === "object" && Object.keys(tool.args).length > 0;
                    const hasResult = typeof tool.result !== "undefined" && tool.result !== null;
                    const toolSourceLabel = messageSourceLabel || "";
                    const toolName =
                      tool && typeof tool.name === "string" && tool.name.trim()
                        ? tool.name.trim()
                        : null;
                    const rawRequestId =
                      tool && typeof tool === "object"
                        ? tool.id || tool.request_id || null
                        : null;
                    const requestId = rawRequestId ? String(rawRequestId) : null;
                    const baselineArgs =
                      hasArgs && tool && typeof tool === "object" && tool.args && typeof tool.args === "object"
                        ? tool.args
                        : {};
                    const chainTarget = msg.id || msg.message_id || null;
                    const sessionIdForTool =
                      (tool && typeof tool.session_id === "string" && tool.session_id) ||
                      (msg && typeof msg.session_id === "string" && msg.session_id) ||
                      (typeof state.sessionId === "string" && state.sessionId) ||
                      null;
                    const previewText = hasResult
                      ? summarizeToolValue(tool.result, toolName)
                      : hasArgs
                        ? summarizeToolValue(tool.args, toolName)
                        : "";
                    const invokeDirect = async (overrideArgs, overrideName) => {
                      if (!toolName || !chainTarget) return;
                      const payload = {
                        name: (overrideName || toolName || "").trim() || toolName,
                        args: overrideArgs ?? baselineArgs ?? {},
                        chain_id: chainTarget,
                      message_id: chainTarget,
                      ...(sessionIdForTool ? { session_id: sessionIdForTool } : {}),
                    };
                    try {
                      const resp = await axios.post("/api/tools/invoke", payload);
                      return { result: resp?.data?.result, status: "invoked" };
                    } catch (err) {
                      console.error("Tool invoke failed", err);
                      const detail =
                        err?.response?.data?.detail ||
                        err?.response?.data?.message ||
                        err?.message ||
                        "Tool invoke failed.";
                      const statusCode = err?.response?.status;
                      const safeDetail = statusCode && statusCode >= 500 ? "Tool error." : detail;
                      setBanner({
                        message: `Tool invoke failed: ${detail}`,
                        category: "tool_error",
                      });
                      return {
                        result: buildToolOutcomeResult("error", safeDetail),
                        status: "error",
                      };
                    }
                  };
                  const submitDecision = async (
                    decision,
                    overrideArgs,
                    overrideName,
                    continueTarget,
                  ) => {
                    try {
                      if (requestId) {
                        const effectiveArgs =
                          overrideArgs ??
                          (hasArgs ? baselineArgs ?? {} : undefined);
                        const payload = {
                          request_id: requestId,
                          decision,
                          name: (overrideName || toolName || "").trim() || toolName,
                          session_id: sessionIdForTool,
                          message_id: chainTarget,
                          chain_id: chainTarget,
                        };
                        if (typeof effectiveArgs !== "undefined") {
                          payload.args = effectiveArgs;
                        }
                        const resp = await axios.post("/api/tools/decision", payload);
                        const returnedStatusRaw =
                          typeof resp?.data?.status === "string" ? resp.data.status : "";
                        const returnedStatus = normalizeToolStatus(returnedStatusRaw);
                        const returnedResult =
                          typeof resp?.data?.result !== "undefined" ? resp.data.result : undefined;
                        if (returnedStatus) {
                          setState((prev) => {
                            const updated = Array.isArray(prev.conversation)
                              ? [...prev.conversation]
                              : [];
                            const mIdx = updated.findIndex((m) => m && m.id === chainTarget);
                            if (mIdx === -1) return prev;
                            const msgEntry = { ...(updated[mIdx] || {}) };
                            const existingTools = Array.isArray(msgEntry.tools)
                              ? [...msgEntry.tools]
                              : [];
                            const tIdx = existingTools.findIndex((t) => {
                              if (!t || typeof t !== "object") return false;
                              const rawId = t.id || t.request_id || null;
                              return rawId ? String(rawId) === String(requestId) : false;
                            });
                            if (tIdx === -1) return prev;
                            existingTools[tIdx] = {
                              ...existingTools[tIdx],
                              status: returnedStatus,
                              ...(typeof returnedResult !== "undefined"
                                ? { result: returnedResult }
                                : {}),
                            };
                            msgEntry.tools = existingTools;
                            updated[mIdx] = msgEntry;
                            return { ...prev, conversation: updated };
                          });
                        }
                        if (returnedStatus === "error") {
                          const detail = (() => {
                            if (resp?.data?.error) return resp.data.error;
                            if (returnedResult && typeof returnedResult === "object") {
                              return returnedResult.message || returnedResult.error || "Tool error.";
                            }
                            return returnedResult || "Tool error.";
                          })();
                          setBanner({
                            message: `Tool error: ${detail}`,
                            category: "tool_error",
                          });
                        }
                        const resolvedStatuses = new Set([
                          "invoked",
                          "error",
                          "denied",
                          "cancelled",
                          "timeout",
                          "scheduled",
                          "ok",
                          "success",
                          "complete",
                        ]);
                        if (returnedStatus && resolvedStatuses.has(returnedStatus)) {
                          const toolWithResult = {
                            ...(tool || {}),
                            id: requestId,
                            name: payload.name,
                            args: effectiveArgs ?? baselineArgs ?? {},
                            ...(typeof returnedResult !== "undefined"
                              ? { result: returnedResult }
                              : {}),
                            status: returnedStatus || "invoked",
                          };
                          const baseTools = resolveMessageTools(msg);
                          const toolsForContinue = mergeToolEntries(
                            baseTools,
                            [toolWithResult],
                            msg.metadata,
                          );
                          await maybeContinueAfterTools(
                            { ...msg, tools: toolsForContinue },
                            toolsForContinue,
                            continueTarget,
                          );
                        }
                      } else if (decision === "accept") {
                        const outcome = await invokeDirect(overrideArgs, overrideName);
                        if (outcome && typeof outcome.result !== "undefined") {
                          const toolWithResult = {
                            ...(tool || {}),
                            name: (overrideName || toolName || "").trim() || toolName,
                            args: overrideArgs ?? baselineArgs ?? {},
                            result: outcome.result,
                            status: outcome.status || "invoked",
                          };
                          const baseTools = resolveMessageTools(msg);
                          const toolsForContinue = mergeToolEntries(
                            baseTools,
                            [toolWithResult],
                            msg.metadata,
                          );
                          setState((prev) => {
                            const updated = Array.isArray(prev.conversation)
                              ? [...prev.conversation]
                              : [];
                            const mIdx = updated.findIndex((m) => m && m.id === chainTarget);
                            if (mIdx === -1) return prev;
                            const msgEntry = { ...(updated[mIdx] || {}) };
                            msgEntry.tools = toolsForContinue;
                            updated[mIdx] = msgEntry;
                            return { ...prev, conversation: updated };
                          });
                          await maybeContinueAfterTools(
                            { ...msg, tools: toolsForContinue },
                            toolsForContinue,
                            continueTarget,
                          );
                        }
                      }
                    } catch (err) {
                      console.error("Tool decision failed", err);
                      const detail =
                        err?.response?.data?.detail ||
                        err?.response?.data?.message ||
                        err?.message ||
                        "Tool decision failed.";
                      setBanner({
                        message: `Tool decision failed: ${detail}`,
                        category: "tool_error",
                      });
                      if (requestId) {
                        setState((prev) => {
                          const updated = Array.isArray(prev.conversation)
                            ? [...prev.conversation]
                            : [];
                          const mIdx = updated.findIndex((m) => m && m.id === chainTarget);
                          if (mIdx === -1) return prev;
                          const msgEntry = { ...(updated[mIdx] || {}) };
                          const existingTools = Array.isArray(msgEntry.tools)
                            ? [...msgEntry.tools]
                            : [];
                          const tIdx = existingTools.findIndex((t) => {
                            if (!t || typeof t !== "object") return false;
                            const rawId = t.id || t.request_id || null;
                            return rawId ? String(rawId) === String(requestId) : false;
                          });
                          if (tIdx === -1) return prev;
                          existingTools[tIdx] = {
                            ...existingTools[tIdx],
                            status: "error",
                            result: String(detail),
                          };
                          msgEntry.tools = existingTools;
                          updated[mIdx] = msgEntry;
                          return { ...prev, conversation: updated };
                        });
                      }
                    }
                  };
                    return (
                      <details
                        key={`tool-${i}`}
                        className={`inline-tool compact status-${statusTone}${
                          toolsCollapsed ? " collapsed" : ""
                        }${
                          isActiveMessage ? " active" : ""
                        }`}
                        open={isActiveMessage && !toolsCollapsed}
                      >
                      <summary className="tool-summary compact">
                        <div className="tool-summary-main">
                          <div className="tool-meta">
                            <span className="tool-step-index">{i + 1}</span>
                            <span className="tool-name">{tool.name || "tool"}</span>
                            <span className={`tool-status-badge status-${statusTone}`}>
                              <span className="tool-status-glyph" aria-hidden="true">
                                {statusGlyph}
                              </span>
                              {statusLabel}
                            </span>
                          </div>
                          {previewText && (
                            <span className="tool-preview" title={previewText}>
                              {previewText}
                            </span>
                          )}
                        </div>
                        {isPending && (
                          <div className="tool-actions inline">
                            <button
                              type="button"
                              className="tool-action-btn accept"
                              onClick={async (event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                await submitDecision("accept");
                              }}
                            >
                              Accept
                            </button>
                            <button
                              type="button"
                              className="tool-action-btn deny"
                              disabled={!requestId}
                              onClick={async (event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (!requestId) return;
                                await submitDecision("deny");
                              }}
                            >
                              Deny
                            </button>
                            <button
                              type="button"
                              className="tool-action-btn edit"
                              onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                setToolEditorState({
                                  tool: {
                                    name: toolName,
                                    args: baselineArgs || {},
                                    id: requestId,
                                    status,
                                  },
                                  onSubmit: async ({ args, name, continueTarget }) => {
                                    await submitDecision(
                                      "accept",
                                      args,
                                      name,
                                      continueTarget,
                                    );
                                  },
                                  schedulePrefill: {
                                    start_time: Math.floor(Date.now() / 1000),
                                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                                    title: `Schedule tool: ${toolName || "tool"}`,
                                  },
                                  onSchedule: async ({ args, name, schedule }) => {
                                    if (!schedule || !schedule.event_id) {
                                      throw new Error("Missing schedule details.");
                                    }
                                    const eventId = String(schedule.event_id);
                                    const resolvedName =
                                      (name || toolName || "").trim() || toolName || "tool";
                                    const continueInline =
                                      schedule.conversation_mode !== "new_chat";
                                    try {
                                      await axios.post(
                                        `/api/calendar/events/${encodeURIComponent(eventId)}`,
                                        {
                                          id: eventId,
                                          title: schedule.title || `Schedule tool: ${resolvedName}`,
                                          description: schedule.description,
                                          location: schedule.location,
                                          start_time: schedule.start_time,
                                          end_time: schedule.end_time,
                                          timezone: schedule.timezone,
                                          status: schedule.status || "scheduled",
                                        },
                                      );
                                      const reqId = requestId ? String(requestId) : eventId;
                                      await axios.post("/api/tools/schedule", {
                                        request_id: reqId,
                                        event_id: eventId,
                                        name: resolvedName,
                                        args: args || {},
                                        prompt: schedule.prompt,
                                        conversation_mode: schedule.conversation_mode,
                                        session_id: continueInline
                                          ? sessionIdForTool || state.sessionId
                                          : undefined,
                                        message_id: continueInline ? chainTarget : undefined,
                                        chain_id: continueInline ? chainTarget : undefined,
                                      });
                                      if (chainTarget && requestId) {
                                        setState((prev) => {
                                          const updated = Array.isArray(prev.conversation)
                                            ? [...prev.conversation]
                                            : [];
                                          const idx = updated.findIndex(
                                            (m) => m && m.id === chainTarget,
                                          );
                                          if (idx === -1) return prev;
                                          const tools = Array.isArray(updated[idx]?.tools)
                                            ? [...updated[idx].tools]
                                            : [];
                                          const tIdx = tools.findIndex(
                                            (t) =>
                                              t &&
                                              (t.id === requestId ||
                                                t.request_id === requestId),
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
                                    }
                                  },
                                });
                              }}
                            >
                              Edit
                            </button>
                          </div>
                        )}
                        <span className="tool-summary-caret" aria-hidden="true">
                          {">"}
                        </span>
                      </summary>
                      {(hasArgs || hasResult || toolSourceLabel) && (
                        <div className="tool-content">
                          {toolSourceLabel && (
                            <div className="tool-source">source: {toolSourceLabel}</div>
                          )}
                          {hasArgs && (
                            <pre className="tool-args-inline" aria-label="Tool arguments">
                              {formatToolPayload(tool.args)}
                            </pre>
                          )}
                          {hasResult && (
                            (() => {
                              const toolLabel =
                                typeof tool.name === "string" ? tool.name.toLowerCase() : "";
                              const renderStructuredResult =
                                toolLabel.startsWith("computer.") || toolLabel === "open_url";
                              if (!renderStructuredResult) {
                                return (
                                  <pre className="tool-result-inline" aria-label="Tool result">
                                    {formatToolPayload(tool.result)}
                                  </pre>
                                );
                              }
                              return (
                                <div className="tool-result-inline" aria-label="Tool result">
                                  <ToolPayloadView
                                    value={tool.result}
                                    toolName={tool.name}
                                    kind="result"
                                    compact
                                    onOpenComputerSession={openBrowserSessionInspector}
                                  />
                                </div>
                              );
                            })()
                          )}
                        </div>
                      )}
                    </details>
                    );
                  })}
                  </div>
                );
              })()}
              {isStreaming &&
                idx === state.conversation.length - 1 &&
                msg.role === "ai" && <span className="spinner" />}
              {(msg.timestamp || msg.role === "ai" || msg.role === "user") && (
                <div className="message-meta">
                  {msg.timestamp && timestampLabel && (
                    <time className="timestamp" dateTime={msg.timestamp} title={timestampTitle}>
                      {timestampLabel}
                    </time>
                  )}
                  {messageSourceLabel && (
                    <span className="message-source" title={`source: ${messageSourceLabel}`}>
                      {messageSourceLabel}
                    </span>
                  )}
                  {messageStatusBadge && (
                    <span
                      className={`message-status-chip message-status-${messageStatusBadge.tone}`}
                      title={messageStatusBadge.title}
                    >
                      {messageStatusBadge.label}
                    </span>
                  )}
                  {msg.role === "user" && (
                    <div className="message-actions">
                      <Tooltip title="Edit this user message and regenerate">
                        <IconButton
                          className="regen-btn"
                          aria-label="Edit user message"
                          onClick={() => openEditUserMessage(msg)}
                          size="small"
                          style={{ color: "var(--color-accent)" }}
                        >
                          <EditOutlinedIcon fontSize="inherit" />
                        </IconButton>
                      </Tooltip>
                    </div>
                  )}
                  {msg.role === "ai" && (() => {
                    const ttsId = msg.id || msg.message_id || null;
                    const isTtsActive = ttsPlayback.messageId === ttsId;
                    const unresolvedLoop =
                      !!(msg.metadata && typeof msg.metadata === "object" && msg.metadata.unresolved_tool_loop);
                    const progress =
                      isTtsActive && ttsPlayback.duration > 0
                        ? Math.min(
                            100,
                            Math.max(
                              0,
                              (ttsPlayback.currentTime / ttsPlayback.duration) * 100,
                            ),
                          )
                        : 0;
                    return (
                    <div className="message-actions">
                      {canContinueMessage(msg) && (
                        <Tooltip
                          title={
                            unresolvedLoop
                              ? "Retry continuation using the latest tool outcomes"
                              : activeModelLabel
                                ? `Continue with ${activeModelLabel}`
                                : "Continue generating after tool results"
                          }
                        >
                          <button
                            type="button"
                            className={`chip msg-action-chip${unresolvedLoop ? " retry" : ""}`}
                            onClick={() => continueGenerating(msg)}
                            disabled={loading}
                            aria-label="Continue generating"
                          >
                            {unresolvedLoop ? "retry continue" : "continue"}
                          </button>
                        </Tooltip>
                      )}
                      <Tooltip
                        title={
                          isTtsActive && ttsPlayback.status !== "loading"
                            ? "Pause/resume speech"
                            : "Speak this response"
                        }
                      >
                        <IconButton
                          className="regen-btn"
                          aria-label="Speak assistant response"
                          onClick={() => speakAssistantMessage(msg)}
                          size="small"
                          disabled={
                            loading ||
                            !(
                              (typeof msg.text === "string" && msg.text.trim()) ||
                              (typeof msg.content === "string" && msg.content.trim())
                            )
                          }
                          style={{ color: "var(--color-accent)" }}
                        >
                          {isTtsActive && ttsPlayback.status !== "loading" ? (
                            <PauseCircleFilledIcon fontSize="inherit" />
                          ) : (
                            <VolumeUpIcon fontSize="inherit" />
                          )}
                        </IconButton>
                      </Tooltip>
                      {isTtsActive && (
                        <div className="tts-progress">
                          <div className="tts-progress-track" aria-hidden="true">
                            <div
                              className="tts-progress-fill"
                              style={{ width: `${progress}%` }}
                            />
                          </div>
                          <span className="tts-progress-time">
                            {formatDuration(ttsPlayback.currentTime)} /{" "}
                            {formatDuration(ttsPlayback.duration)}
                          </span>
                        </div>
                      )}
                      <Tooltip title="Edit this assistant response">
                        <IconButton
                          className="regen-btn"
                          aria-label="Edit assistant response"
                          onClick={() => openEditAssistantMessage(msg)}
                          size="small"
                          disabled={loading}
                          style={{ color: "var(--color-accent)" }}
                        >
                          <EditOutlinedIcon fontSize="inherit" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip
                        title={
                          activeModelLabel
                            ? `Regenerate with ${activeModelLabel}`
                            : "Regenerate this response"
                        }
                      >
                        <IconButton
                          className="regen-btn"
                          aria-label="Regenerate response"
                          onClick={() => regenerateMessage(msg)}
                          size="small"
                          style={{ color: "var(--color-accent)" }}
                        >
                          <RefreshRoundedIcon fontSize="inherit" />
                        </IconButton>
                      </Tooltip>
                    </div>
                    );
                  })()}
                </div>
              )}
              </div>
            {idx < state.conversation.length - 1 && (
              <Divider className="chat-divider" />
            )}
            </React.Fragment>
        );
        })}
        <div ref={bottomSentinelRef} className="chat-bottom-sentinel" />
      </div>
      {/* input moved to portal */}
    </div>
    {toolEditorState && (
      <ToolEditorModal
        open
        tool={toolEditorState.tool}
        schedulePrefill={toolEditorState.schedulePrefill}
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
    {messageEditorState && (
      <div
        className="message-editor-overlay"
        role="presentation"
        onClick={() => setMessageEditorState(null)}
      >
        <section
          className="message-editor"
          role="dialog"
          aria-modal="true"
          aria-label="Edit message"
          onClick={(e) => e.stopPropagation()}
        >
          <header className="message-editor-header">
            <div>
              <p className="message-editor-label">Edit message</p>
              <p className="message-editor-meta">
                {messageEditorState.mode === "assistant"
                  ? "Update the assistant text (transcript edit)."
                  : "Update the user text, then regenerate the assistant response."}
              </p>
            </div>
            <button
              type="button"
              className="message-editor-close"
              aria-label="Close message editor"
              onClick={() => setMessageEditorState(null)}
            >
              &times;
            </button>
          </header>
          <textarea
            className="message-editor-textarea"
            rows={10}
            value={messageEditorState.text}
            onChange={(e) =>
              setMessageEditorState((prev) => ({ ...prev, text: e.target.value }))
            }
          />
          <div className="message-editor-actions">
            <button
              type="button"
              className="chip msg-action-chip"
              onClick={() => setMessageEditorState(null)}
            >
              cancel
            </button>
            <button
              type="button"
              className="chip msg-action-chip"
              onClick={async () => {
                const assistantId = messageEditorState.assistantId;
                const text = messageEditorState.text || "";
                const mode = messageEditorState.mode;
                setMessageEditorState(null);
                if (mode === "assistant") {
                  applyAssistantEdit(assistantId, text);
                  return;
                }
                const assistantMsg = state.conversation.find((m) => m && m.id === assistantId);
                if (assistantMsg) {
                  await regenerateMessage(assistantMsg, { overrideUserText: text });
                }
              }}
            >
              {messageEditorState.mode === "assistant" ? "apply" : "apply & regenerate"}
            </button>
          </div>
        </section>
      </div>
    )}
    {!entryOpen && error && <p className="error">{error}</p>}
    {typeof document !== 'undefined' && createPortal(
      (entryOpen ? (
        <div
          className={`input-box${cameraOpen ? " camera-open" : ""}`}
          ref={inputBoxRef}
          onMouseEnter={() => {
            if (entryHoverTimer.current) {
              clearTimeout(entryHoverTimer.current);
              entryHoverTimer.current = null;
            }
          }}
        >
          <div
            className={`composer-resize-edge${cameraOpen ? " is-disabled" : ""}`}
            role="separator"
            aria-orientation="horizontal"
            aria-label="Drag to resize composer"
            aria-disabled={cameraOpen ? "true" : "false"}
            tabIndex={cameraOpen ? -1 : 0}
            title={
              cameraOpen
                ? "Composer resize is disabled while the camera preview is open"
                : "Drag this edge upward to expand the composer. Press Home to reset."
            }
            onMouseDown={(event) => {
              if (cameraOpen) return;
              event.preventDefault();
              startComposerResize(event.clientY, "mouse");
            }}
            onTouchStart={(event) => {
              if (cameraOpen || !event.touches || !event.touches[0]) return;
              event.preventDefault();
              startComposerResize(event.touches[0].clientY, "touch");
            }}
            onDoubleClick={() => {
              if (!cameraOpen) setComposerRows(DEFAULT_COMPOSER_ROWS);
            }}
            onKeyDown={handleComposerResizeKeyDown}
          />
          {error && (
            <div className="input-error" role="alert">
              {error}
            </div>
          )}
          {banner && (
            <div className="alert" role="status" style={{ marginBottom: 8 }}>
              <strong>{banner.message}</strong>
              {banner.hint && <span style={{ marginLeft: 8 }}>{banner.hint}</span>}
              <span style={{ marginLeft: 8, display: 'inline-flex', gap: 6 }}>
                {banner.actions && banner.actions.map((a, i) => (
                  <button key={i} className="chip" onClick={a.onClick}>{a.label}</button>
                ))}
              </span>
              <button className="chip" style={{ float: 'right' }} onClick={() => setBanner(null)}>dismiss</button>
            </div>
          )}
          {cameraError && (
            <div className="input-error" role="alert">
              {cameraError}
            </div>
          )}
          {liveVisualError && (
            <div className="input-error" role="alert">
              {liveVisualError}
            </div>
          )}
          {liveTranscriptVisible && (
            <div className="live-transcript-panel" aria-live="polite">
              <div className="live-transcript-header">
                <span className="live-transcript-badge">live transcript</span>
                <span className="live-transcript-status">{liveStreamingStatusLabel}</span>
              </div>
              <div className="live-transcript-row">
                <span className="live-transcript-speaker">you</span>
                <span className="live-transcript-text">
                  {liveStreamingTranscript.user?.trim() || "waiting for speech..."}
                </span>
              </div>
              <div className="live-transcript-row">
                <span className="live-transcript-speaker">float</span>
                <span className="live-transcript-text">
                  {liveStreamingTranscript.assistant?.trim() ||
                    (liveStreamingPhase === "assistant-speaking" ||
                    liveStreamingPhase === "assistant-thinking"
                      ? "responding..."
                      : "waiting")}
                </span>
              </div>
            </div>
          )}
          {recording && liveVisualMode !== "off" && (
            <div className="live-visual-preview-panel">
              <video
                ref={liveVisualPreviewRef}
                className="live-visual-preview-video"
                autoPlay
                playsInline
                muted
              />
              <div className="live-visual-preview-meta">
                <strong>{liveVisualMode === "screen" ? "desktop live" : "camera live"}</strong>
                <span>{liveVisualMode === "screen" ? "streaming shared screen" : "streaming camera input"}</span>
              </div>
            </div>
          )}
          {cameraOpen && (
            <div className="camera-capture-panel">
              <div className="camera-capture-stage">
                <video
                  ref={cameraVideoRef}
                  className="camera-capture-preview"
                  autoPlay
                  playsInline
                  muted
                />
                <div className="camera-capture-overlay camera-capture-overlay-top">
                  <Tooltip title="Close camera and release the device" placement="top" arrow>
                    <button
                      type="button"
                      className="camera-control-button"
                      onClick={stopCameraCapture}
                      aria-label="Close camera preview"
                    >
                      <CloseIcon fontSize="small" />
                    </button>
                  </Tooltip>
                </div>
                <div className="camera-capture-overlay camera-capture-overlay-bottom">
                  <button
                    type="button"
                    className="camera-shutter-button"
                    onClick={captureCameraFrame}
                  >
                    capture
                  </button>
                </div>
              </div>
            </div>
          )}
          {(attachments.length > 0 || hasImageAttachments) && (
            <div className="composer-meta-row">
              {attachments.length > 0 && (
                <div className="attachments-row" aria-live="polite">
                  {attachments.map((att) => (
                    <div
                      key={att.id}
                      className="attachment-chip"
                      title={att.file?.name || "attachment"}
                      onClick={() => window.open(att.remoteUrl || att.url, "_blank", "noopener")}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          window.open(att.remoteUrl || att.url, "_blank", "noopener");
                        }
                      }}
                    >
                      {att.file && att.file.type?.startsWith("image") ? (
                        <img src={att.url} alt="preview" className="chip-thumb" />
                      ) : (
                        <span className="chip-icon" aria-hidden>
                          <AttachFileIcon fontSize="inherit" />
                        </span>
                      )}
                      <span className="chip-name">
                        {truncateFilename(att.file?.name || att.name)}
                      </span>
                      {att.uploading && (
                        <span className="chip-uploading" aria-live="polite">
                          uploading{"\u2026"}
                        </span>
                      )}
                      <button
                        type="button"
                        className="chip-remove"
                        aria-label={`Remove ${att.file?.name || "attachment"}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          removeAttachment(att.id);
                        }}
                      >
                        <CloseIcon fontSize="small" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {hasImageAttachments && (
                <div className="vision-workflow-row">
                  <label
                    htmlFor="vision-workflow-select"
                    className="vision-workflow-label"
                    title={VISION_WORKFLOW_FIELD_DESCRIPTION}
                  >
                    Vision mode
                  </label>
                  <select
                    id="vision-workflow-select"
                    value={visionWorkflow}
                    onChange={(event) => setVisionWorkflow(event.target.value)}
                    title={`${VISION_WORKFLOW_FIELD_DESCRIPTION} ${selectedVisionWorkflow.description}`}
                  >
                    {VISION_WORKFLOW_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <span
                    className="vision-workflow-current"
                    title={selectedVisionWorkflow.description}
                  >
                    {selectedVisionWorkflow.description}
                  </span>
                </div>
              )}
            </div>
          )}
          <div className="input-row">
            <div className="input-main">
              <TextField
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handleComposerPaste}
                disabled={loading && !isStreaming}
                placeholder="Type your message..."
                size="medium"
                multiline
                inputRef={composerInputRef}
                minRows={effectiveComposerRows}
                maxRows={effectiveComposerRows}
                fullWidth
              />
            </div>
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              multiple
              className="hidden-input"
            />
            <div className="input-actions">
              <div className="composer-action-row composer-action-row-top">
                <Tooltip title="Close chat input">
                  <button
                    className="close-entry"
                    onClick={() => setEntryOpen(false)}
                    aria-label="Close chat input"
                  >
                    <CloseIcon fontSize="small" />
                  </button>
                </Tooltip>
                <div className="chat-settings-menu" ref={chatSettingsMenuRef}>
                  <Tooltip title="Chat settings">
                    <button
                      ref={chatSettingsTriggerRef}
                      type="button"
                      className={`chat-settings-trigger${
                        chatSettingsOpen ? " is-open" : ""
                      }`}
                      onClick={() => {
                        setChatSettingsOpen((prev) => !prev);
                        setChatSettingsSection((prev) => prev || "camera");
                      }}
                      aria-label="Chat settings"
                    >
                      <TuneIcon fontSize="small" />
                    </button>
                  </Tooltip>
                </div>
              </div>
              <div className="composer-action-row composer-action-row-bottom">
                <Tooltip
                  title={
                    audioRecording
                      ? "Stop recording audio message"
                      : "Record audio message"
                  }
                >
                  <IconButton
                    onClick={toggleAudioRecording}
                    color={audioRecording ? "error" : "default"}
                    aria-label="record audio message"
                    className="action-icon"
                  >
                    <MicIcon />
                  </IconButton>
                </Tooltip>
                <Tooltip
                  title={
                    liveStreamingActive
                      ? "Stop live streaming mode"
                      : "Start live streaming mode"
                  }
                >
                  <IconButton
                    onClick={toggleRecording}
                    color={liveStreamingActive ? "secondary" : "default"}
                    aria-label="live streaming mode"
                    className={`action-icon live-stream-toggle${
                      liveStreamingActive ? " is-live-streaming" : ""
                    }${speaking ? " is-speaking" : ""}`}
                  >
                    <FiberManualRecordIcon />
                  </IconButton>
                </Tooltip>
                <div className="attach-menu" ref={attachmentMenuRef}>
                  {attachmentMenuOpen && (
                    <div className="attach-popover" role="menu">
                      <Tooltip title="Attach file">
                        <IconButton
                          onClick={handleAttachmentFileAction}
                          aria-label="attach file"
                          className="action-icon"
                        >
                          <AttachFileIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip
                        title={
                          recording
                            ? liveVisualMode === "camera"
                              ? "Turn live camera off"
                              : "Turn live camera on"
                            : cameraOpen
                              ? "Close camera capture"
                            : "Capture from camera"
                        }
                      >
                        <span>
                          <IconButton
                            onClick={handleAttachmentCameraAction}
                            aria-label="capture from camera"
                            className={`action-icon visual-stream-toggle${
                              recording ? " is-live-option" : ""
                            }${
                              recording && liveVisualMode !== "camera" ? " is-off" : ""
                            }${
                              recording && liveVisualMode === "camera" ? " is-on" : ""
                            }`}
                            disabled={cameraBusy}
                          >
                            <PhotoCameraIcon />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip
                        title={
                          recording
                            ? liveVisualMode === "screen"
                              ? "Turn desktop capture off"
                              : "Turn desktop capture on"
                            : "Capture from desktop"
                        }
                      >
                        <span>
                          <IconButton
                            onClick={handleAttachmentScreenAction}
                            aria-label="capture from desktop"
                            className={`action-icon visual-stream-toggle${
                              recording ? " is-live-option" : ""
                            }${
                              recording && liveVisualMode !== "screen" ? " is-off" : ""
                            }${
                              recording && liveVisualMode === "screen" ? " is-on" : ""
                            }`}
                            disabled={screenCaptureBusy}
                          >
                            <ScreenShareIcon />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </div>
                  )}
                  <Tooltip title="Attachments">
                    <IconButton
                      onClick={() => setAttachmentMenuOpen((prev) => !prev)}
                      aria-label="open attachments"
                      className={`action-icon attach-trigger${
                        attachmentMenuOpen ? " is-open" : ""
                      }`}
                    >
                      <AttachFileIcon />
                    </IconButton>
                  </Tooltip>
                </div>
                <div className="send-stack">
                  {isStreaming && (
                    <Tooltip title="Stop generation">
                      <button
                        type="button"
                        className="chip stop-chip"
                        onClick={cancelGeneration}
                        aria-label="Stop generation"
                      >
                        stop
                      </button>
                    </Tooltip>
                  )}
                  <Tooltip title={sendTooltip}>
                    <span>
                      <Button
                        onClick={() => (isStreaming ? cancelGeneration() : sendMessage())}
                        disabled={sendDisabled}
                        variant="contained"
                        color="primary"
                        className={`send-btn ${isStreaming ? "is-stopping" : ""}${
                          sendDisabled && !isStreaming ? " is-idle-disabled" : ""
                        }`}
                        sx={{ minWidth: "32px", padding: "4px" }}
                        aria-label={isStreaming ? "Stop generation" : "Send message"}
                      >
                        {isStreaming ? <StopIcon /> : <SendIcon />}
                      </Button>
                    </span>
                  </Tooltip>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <button
          className="open-entry-btn"
          onClick={() => setEntryOpen(true)}
          aria-label="Open chat input"
          title="Open chat input"
          onMouseEnter={() => {
            if (entryHoverTimer.current) clearTimeout(entryHoverTimer.current);
            entryHoverTimer.current = setTimeout(() => setEntryOpen(true), 600);
          }}
          onMouseLeave={() => {
            if (entryHoverTimer.current) {
              clearTimeout(entryHoverTimer.current);
              entryHoverTimer.current = null;
            }
          }}
        >
          Chat
        </button>
      )), document.body)}
      {chatSettingsPopover}
      {browserSessionPopup &&
        typeof document !== "undefined" &&
        createPortal(
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
            idPrefix="chat-browser-session"
          />,
          document.body,
        )}
      {scrollToBottomButton &&
        createPortal(scrollToBottomButton, document.body)}
    </>
  );
};

export default Chat;
