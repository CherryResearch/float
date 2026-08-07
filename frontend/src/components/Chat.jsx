import React, { useState, useEffect, useContext, useRef, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createPortal } from "react-dom"; // fix: portal used without import
import "../styles/Chat.css";
import "../styles/ProgressBar.css";
import "../styles/ToolActions.css";
import "../styles/ToolPayload.css";
import MediaViewer from "./MediaViewer";
import BrowserSessionDialog from "./BrowserSessionDialog";
import RagContextPanel, { normalizeRagMatches } from "./RagContextPanel";
import axios from "axios";
import { Room, RoomEvent } from "livekit-client";
import {
  debugLog,
  getConversationMessageLimit,
  memoryStore,
  apiWrapper,
} from "../utils/proxy";
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
import StateInspector from "./StateInspector";
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
  acquireToolContinuationLock,
  announceToolContinuationAttemptReset,
  buildToolContinuationLockKey,
  buildToolContinuationSignature,
  hasMatchingToolContinuationSignature,
  releaseToolContinuationLock,
  stableValue,
} from "../utils/toolContinuations";
import {
  TOOL_REVIEW_ACTION_EVENT,
  normalizeToolReviewAction,
  normalizeToolReviewTarget,
  toolReviewScopeSelectors,
} from "../utils/toolReviewActions";
import {
  appendToolContinuationPhase,
  isContinuationPlaceholderText,
  mergeContinuationText,
  normalizeToolContinuationPhases,
} from "../utils/continuationText";
import {
  buildHistoryFromConversation,
  hasRenderableAssistantContent,
} from "../utils/conversationHistory";
import {
  formatApiModelLabel,
  resolveRequestModelForMode,
} from "../utils/modelUtils";
import { getMessageVisionNotice } from "../utils/visionDelivery";
import {
  canResizeChatWindow,
  CHAT_WINDOW_KEYBOARD_STEP,
  CHAT_WINDOW_KEYBOARD_STEP_FAST,
  CHAT_WINDOW_STORAGE_KEY,
  clampChatWindowWidth,
  getChatWindowWidthBounds,
} from "../utils/chatWindowSizing";
import { resolveAnchoredPopoverPosition } from "../utils/popoverPosition";
import {
  FALLBACK_WORKFLOW_PROFILES,
  isWorkflowSelectableInChat,
  normalizeWorkflowProfiles,
  resolveSelectableWorkflowId,
} from "../utils/workflowCatalog";
import {
  isCustomReasoningEffort,
  normalizeThinkingMode,
  REASONING_EFFORT_PRESETS,
  reasoningEffortValue,
  thinkingPayloadForMode,
} from "../utils/reasoningEffort";
import {
  formatTokenLimit,
  MAX_CUSTOM_OUTPUT_TOKENS,
  normalizeCustomOutputTokens,
  normalizeOutputTokenMode,
  OUTPUT_TOKEN_PRESETS,
  outputTokenPayload,
  resolveModelCapabilities,
  selectedOutputTokenLimit,
} from "../utils/generationLimits";
import {
  ATTACHMENT_OUTBOX_TTL_MS,
  cleanupExpiredAttachmentOutboxEntries,
  deleteAttachmentOutboxEntry,
  deleteSentAttachmentOutboxEntries,
  listAttachmentOutboxEntries,
  putAttachmentOutboxEntry,
} from "../utils/attachmentOutbox";

const DEFAULT_COMPOSER_ROWS = 4;
const MAX_COMPOSER_ROWS = 72;
const DEFAULT_TTS_MODEL = "tts-1";
const DEFAULT_TTS_VOICE = "alloy";
const EMPTY_GLOBAL_STATE = Object.freeze({
  conversation: [],
  history: [],
});
const EMPTY_CONVERSATION = Object.freeze([]);
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
const COMMAND_COMPLETION_LIMIT = 8;
const COMMAND_REFERENCE_RE = /(^|[\s\n])(\.\/\/|\.\/|\/\/)(\[[^\]\n]+\]|[^\s]+)/g;
const TOOL_DIRECTIVE_RE = /^(\s*)%([a-z0-9._-]+)(?:\s+([\s\S]*))?$/i;
const TOOL_DIRECTIVE_TOKEN_RE = /(^|[\s\n])%([a-z0-9._-]+)(?=$|[\s\n.,;:!?])/gi;
const REQUEST_ACTIVITY_TYPES = new Set(["content", "thought", "tool"]);
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const streamActivityMatchesRequest = (activity, messageId) => {
  if (!activity || typeof activity !== "object") return false;
  if (!REQUEST_ACTIVITY_TYPES.has(String(activity.type || "").toLowerCase())) {
    return false;
  }
  const requestMessageId = String(messageId || "").trim();
  if (!requestMessageId) return false;
  return [activity.message_id, activity.chain_id]
    .map((value) => String(value || "").trim())
    .some((value) => value === requestMessageId);
};

const createRequestInactivityTimer = ({ timeoutMs, onTimeout }) => {
  let timer = null;
  let stopped = false;
  const delay = Math.max(1, Number(timeoutMs) || 1);

  const schedule = () => {
    if (stopped) return false;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (stopped) return;
      stopped = true;
      onTimeout();
    }, delay);
    return true;
  };

  schedule();
  return {
    markActivity: schedule,
    clear: () => {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
};
const REASONING_EFFORT_TOOLTIP_TEXT =
  "Reasoning effort and output length are independent. Tinker uses the exact slider value; other supported models round custom values to the nearest named level.";
const RAG_TOOLTIP_TEXT =
  "Memory retrieval searches saved content before the reply. Text models find similar text memories; vision models find similar image memories. Enable a lane, then choose its retrieval model.";
const CHAT_SETTINGS_SECTIONS = [
  ["workflow", "Workflow"],
  ["thinking", "Reasoning & memory"],
  ["camera", "Camera"],
  ["microphone", "Microphone"],
  ["volume", "Voice"],
];
const RAG_TEXT_MODEL_OPTIONS = [
  { value: "simple", label: "Hash fallback", lane: "local" },
  { value: "local:all-MiniLM-L6-v2", label: "all-MiniLM-L6-v2", lane: "local" },
  {
    value: "local:google/embeddinggemma-300M",
    label: "EmbeddingGemma 300M",
    lane: "local",
  },
  { value: "api:text-embedding-3-small", label: "text-embedding-3-small", lane: "api" },
  { value: "api:text-embedding-3-large", label: "text-embedding-3-large", lane: "api" },
];
const RAG_VISION_MODEL_OPTIONS = [
  { value: "ViT-B-32", label: "OpenCLIP ViT-B-32", lane: "local" },
  { value: "ViT-B-16", label: "OpenCLIP ViT-B-16", lane: "local" },
  { value: "ViT-L-14", label: "OpenCLIP ViT-L-14", lane: "local" },
];
const AUDIO_STT_MODEL_OPTIONS = [
  { value: "gpt-realtime-whisper", label: "gpt-realtime-whisper", lane: "api" },
  { value: "whisper-1", label: "whisper-1", lane: "api" },
  { value: "gpt-4o-mini-transcribe", label: "gpt-4o-mini-transcribe", lane: "api" },
  { value: "gpt-4o-transcribe", label: "gpt-4o-transcribe", lane: "api" },
  { value: "whisper-large-v3-turbo", label: "whisper-large-v3-turbo", lane: "local" },
  { value: "whisper-small", label: "whisper-small", lane: "local" },
];
const AUDIO_TTS_MODEL_OPTIONS = [
  { value: "tts-1", label: "tts-1", lane: "api" },
  { value: "tts-1-hd", label: "tts-1-hd", lane: "api" },
  { value: "gpt-4o-mini-tts", label: "gpt-4o-mini-tts", lane: "api" },
  {
    value: "gpt-4o-mini-tts-2025-12-15",
    label: "gpt-4o-mini-tts-2025-12-15",
    lane: "api",
  },
  { value: "kokoro", label: "kokoro", lane: "local" },
  { value: "kitten", label: "kitten", lane: "local" },
];
const OPENAI_TTS_VOICE_OPTIONS = [
  "alloy",
  "ash",
  "ballad",
  "coral",
  "echo",
  "fable",
  "nova",
  "onyx",
  "sage",
  "shimmer",
  "verse",
];
const OPENAI_LEGACY_TTS_VOICE_OPTIONS = [
  "alloy",
  "echo",
  "fable",
  "onyx",
  "nova",
  "shimmer",
];
const KITTEN_TTS_VOICE_OPTIONS = [
  "expr-voice-2-f",
  "expr-voice-3-f",
  "expr-voice-4-f",
  "expr-voice-5-f",
  "expr-voice-2-m",
  "expr-voice-3-m",
  "expr-voice-4-m",
  "expr-voice-5-m",
];
const KOKORO_TTS_VOICE_OPTIONS = ["af_heart", "af_bella", "af_nova", "bf_emma"];
const REALTIME_TOOL_NAME_PATTERN = /^[a-zA-Z0-9_-]+$/;
const LIVE_SESSION_CANCELLED_CODE = "LIVE_SESSION_CANCELLED";
const RESPONSE_FAILURE_METADATA_KEYS = [
  "error",
  "attempts",
  "status_code",
  "category",
  "endpoint",
  "hint",
  "request_id",
  "provider_message",
  "provider_error",
  "provider_error_text",
  "idle_seconds",
  "empty_response",
  "empty_response_reason",
  "warning",
];
const REGENERATION_CONTINUATION_METADATA_KEYS = [
  "tool_continued",
  "tool_continue_signature",
  "tool_continue_semantic_signature",
  "tool_continue_signature_sha256",
  "tool_continue_semantic_signature_sha256",
  "tool_continuation_rounds",
  "tool_continuation_limit_reached",
  "continuation_stop_reason",
  "empty_tool_continuation",
  "unresolved_tool_loop",
  "tool_result_text_retry",
  "tool_result_text_retry_failed",
  "tool_response_pending",
  "inline_tool_continuation_pending",
  "tool_result_continuation_pending",
  "tool_continuation_phases",
  "tool_continuation_text",
  "tool_prelude_text",
];

const createLiveSessionCancelledError = () => {
  const error = new Error("Live streaming start was cancelled.");
  error.code = LIVE_SESSION_CANCELLED_CODE;
  return error;
};

const isLiveSessionCancelledError = (error) =>
  error &&
  (error.code === LIVE_SESSION_CANCELLED_CODE ||
    error.message === "Live streaming start was cancelled.");

const inferAudioModelLane = (model, fallback = "local") => {
  const normalized = String(model || "").trim().toLowerCase();
  if (!normalized) return fallback;
  if (
    normalized.startsWith("api:") ||
    normalized === "whisper-1" ||
    normalized === "gpt-realtime-whisper" ||
    normalized.startsWith("tts-") ||
    normalized.startsWith("gpt-4o") ||
    normalized.includes("transcribe")
  ) {
    return "api";
  }
  return "local";
};

const optionLabelWithLane = (option) =>
  `${option.label || option.value} (${option.lane === "api" ? "API" : "Local"})`;

const voiceOptionsForTtsModel = (modelValue) => {
  const normalized = String(modelValue || "").trim().toLowerCase();
  if (normalized === "tts-1" || normalized === "tts-1-hd") {
    return OPENAI_LEGACY_TTS_VOICE_OPTIONS;
  }
  if (normalized.startsWith("gpt-4o") && normalized.includes("tts")) {
    return OPENAI_TTS_VOICE_OPTIONS;
  }
  if (normalized.includes("kitten")) return KITTEN_TTS_VOICE_OPTIONS;
  if (normalized.includes("kokoro")) return KOKORO_TTS_VOICE_OPTIONS;
  return [
    ...OPENAI_TTS_VOICE_OPTIONS,
    ...KITTEN_TTS_VOICE_OPTIONS,
    ...KOKORO_TTS_VOICE_OPTIONS,
  ];
};

const defaultVoiceForTtsModel = (modelValue, currentVoice = "") => {
  const options = voiceOptionsForTtsModel(modelValue);
  const selected = String(currentVoice || "").trim();
  return selected && options.includes(selected) ? selected : options[0] || selected;
};

const realtimeToolApiName = (name, usedNames = new Set()) => {
  const raw = String(name || "").trim();
  let base = raw.replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/_+/g, "_");
  base = base.replace(/^_+|_+$/g, "");
  if (!base) base = "tool";
  let candidate = base;
  let suffix = 2;
  while (!REALTIME_TOOL_NAME_PATTERN.test(candidate) || usedNames.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  usedNames.add(candidate);
  return candidate;
};

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
  const numericValue =
    typeof value === "number"
      ? value
      : typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value.trim())
        ? Number(value)
        : null;
  const normalizedValue =
    Number.isFinite(numericValue) && Math.abs(numericValue) < 1e12
      ? numericValue * 1000
      : numericValue ?? value;
  const date = value instanceof Date ? value : new Date(normalizedValue);
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

const MermaidBlock = React.memo(({ code }) => {
  const containerRef = React.useRef(null);
  const idRef = React.useRef(
    `merm-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`,
  );

  React.useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container || !window.mermaid || typeof window.mermaid.render !== "function") {
      if (container) container.textContent = code;
      return () => {
        cancelled = true;
      };
    }

    container.textContent = "";
    try {
      const result = window.mermaid.render(idRef.current, code, (svg) => {
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
        }
      });
      if (result && typeof result.then === "function") {
        result
          .then((rendered) => {
            if (!cancelled && containerRef.current) {
              containerRef.current.innerHTML = rendered?.svg || "";
            }
          })
          .catch((err) => {
            console.error("Mermaid render error", err);
            if (!cancelled && containerRef.current) {
              containerRef.current.textContent = code;
            }
          });
      }
    } catch (err) {
      console.error("Mermaid render error", err);
      container.textContent = code;
    }

    return () => {
      cancelled = true;
    };
  }, [code]);

  return <div ref={containerRef} />;
});

MermaidBlock.displayName = "MermaidBlock";

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

const getComposerViewportBounds = () => {
  const visualViewport = window.visualViewport;
  return {
    left: visualViewport?.offsetLeft || 0,
    top: visualViewport?.offsetTop || 0,
    width:
      visualViewport?.width ||
      window.innerWidth ||
      document.documentElement.clientWidth ||
      0,
    height:
      visualViewport?.height ||
      window.innerHeight ||
      document.documentElement.clientHeight ||
      0,
  };
};

const buildComposerOverlayStyle = ({
  anchorRect,
  popoverRect,
  maxWidth,
  gap,
  zIndex,
}) => {
  if (!anchorRect || typeof window === "undefined") {
    return { position: "fixed", top: "12px", left: "12px", zIndex };
  }
  const position = resolveAnchoredPopoverPosition({
    anchorRect,
    popoverRect,
    viewport: getComposerViewportBounds(),
    maxWidth,
    gap,
  });
  return {
    position: "fixed",
    top: `${position.top}px`,
    left: `${position.left}px`,
    maxWidth: `${position.maxWidth}px`,
    maxHeight: `${position.maxHeight}px`,
    zIndex,
  };
};

const createApiWrapperError = (result) => {
  const detail = String(result?.error || "API request failed");
  const error = new Error(detail);
  const status = Number(result?.status);
  if (Number.isFinite(status) && status > 0) {
    error.response = { status, data: { detail } };
  }
  return error;
};

const isRegenerateMessageConflict = (error, detail) => {
  const message = String(detail || "");
  return (
    /(?:message[\s_-]*id|message identifier).*(?:already exists|conflict|duplicate|reserved)/i.test(
      message,
    ) ||
    /only the latest.*(?:can|may) be regenerated/i.test(message) ||
    /(?:saved )?(?:assistant )?(?:message|turn).*(?:not found|no longer exists|stale)/i.test(
      message,
    ) ||
    Number(error?.response?.status) === 409
  );
};

const getRegenerationResponseError = (metadata, responseText = "") => {
  if (!metadata || typeof metadata !== "object") return null;
  const status = String(metadata.status || "").trim().toLowerCase();
  if (metadata.empty_response) {
    const error = new Error(
      "Regeneration completed without returning a final answer.",
    );
    error.code = "EMPTY_REGENERATION_RESPONSE";
    error.metadata = metadata;
    return error;
  }
  if (!metadata.error && status !== "error") return null;
  const error = new Error(
    String(responseText || metadata.error || "Regeneration failed before returning a final answer."),
  );
  error.code = "REGENERATION_PROVIDER_ERROR";
  error.metadata = metadata;
  return error;
};

const getSelectedTargetModelForMode = (state, mode) => {
  return resolveRequestModelForMode({
    backendMode: mode,
    apiModel: state?.apiModel,
    transformerModel: state?.transformerModel,
    localModel: state?.localModel,
  });
};

export const resolveRegenerateRequestTarget = (state) => {
  const currentMode =
    typeof state?.backendMode === "string" && state.backendMode.trim()
      ? state.backendMode.trim().toLowerCase()
      : "api";
  return {
    mode: currentMode,
    model: getSelectedTargetModelForMode(state, currentMode),
  };
};

export const mergeAssistantMessageMetadata = (existingMetadata, nextMetadata) => {
  const existing =
    existingMetadata && typeof existingMetadata === "object" ? { ...existingMetadata } : {};
  const next =
    nextMetadata && typeof nextMetadata === "object" ? { ...nextMetadata } : {};
  const nextStatus =
    typeof next.status === "string" ? next.status.trim().toLowerCase() : "";
  const successful =
    (nextStatus === "complete" || nextStatus === "completed") &&
    !next.error &&
    !next.empty_response;
  if (successful) {
    RESPONSE_FAILURE_METADATA_KEYS.forEach((key) => {
      delete existing[key];
    });
  }
  return { ...existing, ...next };
};

export const clearRegenerationContinuationMetadata = (metadata) => {
  const cleaned = metadata && typeof metadata === "object" ? { ...metadata } : {};
  REGENERATION_CONTINUATION_METADATA_KEYS.forEach((key) => {
    delete cleaned[key];
  });
  return cleaned;
};

export const acknowledgeClientOutboxPair = (conversation, messageId) => {
  const acknowledgedId = String(messageId || "").trim();
  if (!acknowledgedId || !Array.isArray(conversation)) {
    return Array.isArray(conversation) ? conversation : [];
  }
  const acknowledgedIds = new Set([acknowledgedId, `${acknowledgedId}:user`]);
  return conversation.map((item) => {
    if (
      !item ||
      !acknowledgedIds.has(String(item.id || "")) ||
      !item.metadata?.client_outbox
    ) {
      return item;
    }
    const metadata = { ...item.metadata };
    delete metadata.client_outbox;
    return { ...item, metadata };
  });
};

const unwrapCommandValue = (value) => {
  const raw = String(value || "");
  if (raw.startsWith("[") && raw.endsWith("]")) {
    return raw.slice(1, -1);
  }
  return raw;
};

const wrapCommandValue = (value) => {
  const raw = String(value || "");
  if (!raw) return raw;
  return /\s/.test(raw) ? `[${raw}]` : raw;
};

const buildCommandInsertText = (kind, value) => {
  const normalizedValue = String(value || "").trim();
  if (kind === "tool") {
    return `%${normalizedValue} `;
  }
  const prefix = kind === "file" ? "./" : "//";
  return `${prefix}${wrapCommandValue(normalizedValue)} `;
};

const buildFallbackToolArgs = (toolName, body) => {
  const name = String(toolName || "").trim().toLowerCase();
  const trimmed = String(body || "").trim();
  if (!trimmed) return {};
  if (name === "remember") {
    const separator = trimmed.indexOf(":");
    if (separator > 0) {
      const key = trimmed.slice(0, separator).trim();
      const value = trimmed.slice(separator + 1).trim();
      return {
        ...(key ? { key } : {}),
        ...(value ? { value } : {}),
      };
    }
    return { value: trimmed };
  }
  if (name === "search_web") return { query: trimmed };
  if (name === "recall") return { key: trimmed };
  if (name === "tool_help" || name === "tool_info") {
    return trimmed ? { tool_name: trimmed } : {};
  }
  return {};
};

const parseLeadingToolDirective = (text) => {
  const match = String(text || "").match(TOOL_DIRECTIVE_RE);
  if (!match) return null;
  const leadingWhitespace = match[1] || "";
  const toolName = String(match[2] || "").trim();
  if (!toolName) return null;
  const body = typeof match[3] === "string" ? match[3] : "";
  return {
    toolName,
    body,
    start: leadingWhitespace.length,
    prefixEnd: leadingWhitespace.length + 1 + toolName.length,
  };
};

const parseToolDirective = (text) => {
  const value = String(text || "");
  const leading = parseLeadingToolDirective(value);
  if (leading) return leading;

  let match = null;
  TOOL_DIRECTIVE_TOKEN_RE.lastIndex = 0;
  let nextMatch;
  while ((nextMatch = TOOL_DIRECTIVE_TOKEN_RE.exec(value)) !== null) {
    match = nextMatch;
  }
  if (!match) return null;

  const leadingWhitespace = match[1] || "";
  const tokenStart = match.index + leadingWhitespace.length;
  const toolName = String(match[2] || "").trim();
  if (!toolName) return null;
  const tokenEnd = tokenStart + 1 + toolName.length;
  const before = value.slice(0, tokenStart);
  const after = value.slice(tokenEnd);
  const body = `${before}${after}`
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([.,;:!?])/g, "$1")
    .replace(/^[\s.,;:!?]+|[\s.,;:!?]+$/g, "")
    .trim();
  return {
    toolName,
    body,
    start: tokenStart,
    prefixEnd: tokenEnd,
    inline: true,
  };
};

const extractCommandReferences = (text) => {
  const value = String(text || "");
  const matches = [];
  let match;
  COMMAND_REFERENCE_RE.lastIndex = 0;
  while ((match = COMMAND_REFERENCE_RE.exec(value)) !== null) {
    const leading = match[1] || "";
    const prefix = match[2] || "";
    const rawValue = match[3] || "";
    const tokenStart = match.index + leading.length;
    const tokenEnd = tokenStart + prefix.length + rawValue.length;
    const normalizedValue = unwrapCommandValue(rawValue);
    matches.push({
      prefix,
      rawValue,
      value: normalizedValue,
      start: tokenStart,
      end: tokenEnd,
      kind:
        prefix === "./" ? "file" : prefix === "//" ? "memory" : "blended",
    });
  }
  return matches;
};

export const prepareComposerSubmission = (rawMessage, attachmentCount = 0) => {
  const displayMessage = typeof rawMessage === "string" ? rawMessage.trim() : "";
  const normalizedAttachmentCount =
    typeof attachmentCount === "number" && Number.isFinite(attachmentCount)
      ? attachmentCount
      : Array.isArray(attachmentCount)
        ? attachmentCount.length
        : 0;
  return {
    displayMessage,
    shouldSend: Boolean(displayMessage) || normalizedAttachmentCount > 0,
  };
};

export const buildCommandAwareRequest = (displayMessage) => {
  const text = typeof displayMessage === "string" ? displayMessage : "";
  const toolDirective = parseToolDirective(text);
  const references = extractCommandReferences(text).filter(
    (item) => item.kind === "file" || item.kind === "memory",
  );
  const referenceLines = references.map((item) =>
    item.kind === "file"
      ? `- file reference: ${item.value}`
      : `- memory reference: ${item.value}`,
  );
  let requestMessage = text;
  if (toolDirective) {
    const commandBody = toolDirective.body.trim();
    const bodyText =
      commandBody ||
      `The user explicitly requested the ${toolDirective.toolName} tool for this turn.`;
    requestMessage = `${bodyText}\n\nTool preference: prefer the \`${toolDirective.toolName}\` tool for this turn if it fits the request. Do not pretend the tool ran if it did not, and do not force it if another path is clearly better.`;
  }
  if (referenceLines.length) {
    requestMessage = `${requestMessage}\n\nContext references:\n${referenceLines.join("\n")}`;
  }
  return {
    displayMessage: text,
    requestMessage,
    toolDirective,
    references,
  };
};

const responseUsesToolName = (tools, toolName) => {
  const target = String(toolName || "").trim().toLowerCase();
  if (!target) return false;
  return (Array.isArray(tools) ? tools : []).some((tool) => {
    const normalized = normalizeToolEntry(tool);
    return normalized && String(normalized.name || "").trim().toLowerCase() === target;
  });
};

const buildCommandFallbackTool = (toolDirective, messageId) => {
  if (!toolDirective?.toolName) return null;
  const normalizedName = String(toolDirective.toolName || "").trim();
  if (!normalizedName) return null;
  return {
    name: normalizedName,
    args: buildFallbackToolArgs(normalizedName, toolDirective.body || ""),
    status: "proposed",
    synthetic: true,
    synthetic_id: `command-fallback:${messageId || "message"}:${normalizedName}`,
    manual_fill_required: true,
    source: "command_fallback",
    prompt: String(toolDirective.body || "").trim(),
  };
};

const clampCursor = (value, max) => {
  const numeric = Number.isFinite(value) ? value : 0;
  return Math.max(0, Math.min(max, numeric));
};

const findTokenEnd = (text, start) => {
  const value = String(text || "");
  if (start < 0 || start >= value.length) return start;
  let index = start;
  while (index < value.length) {
    const char = value[index];
    if (/\s/.test(char)) return index;
    if (char === "[") {
      const closing = value.indexOf("]", index + 1);
      if (closing === -1) return value.length;
      index = closing + 1;
      continue;
    }
    index += 1;
  }
  return index;
};

const getCommandCompletionContext = (text, cursor) => {
  const value = String(text || "");
  const caret = clampCursor(cursor, value.length);
  let start = caret;
  while (start > 0 && !/\s/.test(value[start - 1])) {
    start -= 1;
  }
  const token = value.slice(start, caret);
  const prefix =
    token.startsWith(".//")
      ? ".//"
      : token.startsWith("./")
        ? "./"
        : token.startsWith("//")
          ? "//"
          : token.startsWith("%")
            ? "%"
            : "";
  if (!prefix) return null;
  const query = unwrapCommandValue(token.slice(prefix.length));
  return {
    prefix,
    kind:
      prefix === "%"
        ? "tool"
        : prefix === "./"
          ? "file"
          : prefix === "//"
            ? "memory"
            : "blended",
    query,
    tokenStart: start,
    tokenEnd: findTokenEnd(value, start),
  };
};

const findLinkedTokenAtCursor = (text, cursor) => {
  const value = String(text || "");
  const caret = clampCursor(cursor, value.length);
  const toolDirective = parseLeadingToolDirective(value);
  if (toolDirective && caret === value.length && toolDirective.body.trim()) {
    return {
      kind: "tool",
      start: toolDirective.start,
      end: value.length,
      prefix: "%",
      rawValue: value.slice(toolDirective.start + 1, value.length),
    };
  }
  const matches = extractCommandReferences(value);
  return (
    matches.find((item) => item.end === caret) || null
  );
};

const unlinkCommandText = (text, cursor) => {
  const value = String(text || "");
  const match = findLinkedTokenAtCursor(value, cursor);
  if (!match) return null;
  if (match.kind === "tool") {
    const percentIndex = value.indexOf("%", match.start);
    if (percentIndex === -1) return null;
    const nextText = `${value.slice(0, percentIndex)}${value.slice(percentIndex + 1)}`;
    return {
      text: nextText,
      cursor: Math.max(0, cursor - 1),
    };
  }
  const rawValue = unwrapCommandValue(match.rawValue);
  const nextText = `${value.slice(0, match.start)}${rawValue}${value.slice(match.end)}`;
  return {
    text: nextText,
    cursor: match.start + rawValue.length,
  };
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
  "response.output_text.delta",
  "response.text.delta",
]);

const LIVE_STREAM_ASSISTANT_TRANSCRIPT_DONE_TYPES = new Set([
  "response.audio_transcript.done",
  "response.output_audio_transcript.done",
  "response.output_text.done",
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

const COMPOSER_DRAFT_STORAGE_PREFIX = "float:chat-composer-draft:v1:";
const COMPOSER_ATTACHMENT_TOMBSTONE_STORAGE_PREFIX =
  "float:chat-composer-attachment-tombstones:v1:";
const MAX_STORED_COMPOSER_ATTACHMENTS = 12;

const getComposerDraftStorage = () => {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
};

const getComposerDraftStorageKey = (sessionId) =>
  `${COMPOSER_DRAFT_STORAGE_PREFIX}${String(sessionId || "default")}`;

const getComposerAttachmentTombstoneStorageKey = (sessionId) =>
  `${COMPOSER_ATTACHMENT_TOMBSTONE_STORAGE_PREFIX}${String(sessionId || "default")}`;

const normalizeStoredString = (value) =>
  typeof value === "string" ? value.trim() : "";

const readStoredComposerAttachmentTombstones = (
  sessionId,
  now = Date.now(),
) => {
  const storage = getComposerDraftStorage();
  if (!storage) return new Set();
  const key = getComposerAttachmentTombstoneStorageKey(sessionId);
  try {
    const parsed = JSON.parse(storage.getItem(key) || "null");
    const updatedAt = Number(parsed?.updatedAt);
    if (
      !Number.isFinite(updatedAt) ||
      updatedAt + ATTACHMENT_OUTBOX_TTL_MS <= now
    ) {
      storage.removeItem(key);
      return new Set();
    }
    return new Set(
      (Array.isArray(parsed?.ids) ? parsed.ids : [])
        .map(normalizeStoredString)
        .filter(Boolean),
    );
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Privacy-restricted storage should degrade to an empty tombstone set.
    }
    return new Set();
  }
};

const markStoredComposerAttachmentTombstones = (
  sessionId,
  attachmentIds = [],
) => {
  const storage = getComposerDraftStorage();
  if (!storage) return;
  const ids = readStoredComposerAttachmentTombstones(sessionId);
  attachmentIds.map(normalizeStoredString).filter(Boolean).forEach((id) => {
    ids.add(id);
  });
  if (!ids.size) return;
  try {
    storage.setItem(
      getComposerAttachmentTombstoneStorageKey(sessionId),
      JSON.stringify({ ids: Array.from(ids), updatedAt: Date.now() }),
    );
  } catch {
    // Storage quota/privacy failures should never block attachment removal.
  }
};

const isPersistableAttachmentUrl = (value) => {
  const url = normalizeStoredString(value);
  return Boolean(url) && !/^blob:/i.test(url) && !/^data:/i.test(url);
};

const normalizeVisionWorkflow = (value) => {
  const normalized = normalizeStoredString(value) || "auto";
  return VISION_WORKFLOW_OPTIONS.some((option) => option.value === normalized)
    ? normalized
    : "auto";
};

const draftAttachmentId = (attachment, index) => {
  const raw =
    attachment?.outboxId ||
    attachment?.outbox_id ||
    attachment?.id ||
    attachment?.contentHash ||
    attachment?.content_hash ||
    attachment?.remoteUrl ||
    attachment?.url ||
    attachment?.name ||
    index;
  const safe = String(raw || index)
    .replace(/[^a-z0-9_-]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return `attachment-draft-${safe || index}`;
};

export const serializeComposerDraftAttachments = (attachments = []) => {
  if (!Array.isArray(attachments)) return [];
  return attachments
    .map((attachment, index) => {
      if (!attachment || typeof attachment !== "object") return null;
      const remoteUrl = isPersistableAttachmentUrl(attachment.remoteUrl)
        ? normalizeStoredString(attachment.remoteUrl)
        : isPersistableAttachmentUrl(attachment.url)
          ? normalizeStoredString(attachment.url)
          : "";
      if (!remoteUrl) return null;
      const name =
        normalizeStoredString(attachment.file?.name) ||
        normalizeStoredString(attachment.name) ||
        "attachment";
      const type =
        normalizeStoredString(attachment.file?.type) ||
        normalizeStoredString(attachment.type);
      const size =
        typeof attachment.file?.size === "number"
          ? attachment.file.size
          : typeof attachment.size === "number"
            ? attachment.size
            : null;
      const contentHash =
        normalizeStoredString(attachment.contentHash) ||
        normalizeStoredString(attachment.content_hash);
      return {
        id: draftAttachmentId(attachment, index),
        outboxId:
          normalizeStoredString(attachment.outboxId) ||
          normalizeStoredString(attachment.outbox_id) ||
          null,
        outbox_id:
          normalizeStoredString(attachment.outboxId) ||
          normalizeStoredString(attachment.outbox_id) ||
          null,
        name,
        type,
        size,
        url: remoteUrl,
        remoteUrl,
        contentHash: contentHash || null,
        content_hash: contentHash || null,
        origin: normalizeStoredString(attachment.origin) || null,
        relative_path:
          normalizeStoredString(attachment.relative_path) ||
          normalizeStoredString(attachment.relativePath) ||
          null,
        source_url:
          normalizeStoredString(attachment.source_url) ||
          normalizeStoredString(attachment.sourceUrl) ||
          null,
        source_url_recorded_at:
          normalizeStoredString(attachment.source_url_recorded_at) ||
          normalizeStoredString(attachment.sourceUrlRecordedAt) ||
          null,
        capture_source:
          normalizeStoredString(attachment.capture_source) ||
          normalizeStoredString(attachment.captureSource) ||
          null,
        capture_id:
          normalizeStoredString(attachment.capture_id) ||
          normalizeStoredString(attachment.captureId) ||
          null,
        transient: attachment.transient === true,
        expires_at: normalizeStoredString(attachment.expires_at) || null,
        caption_status: normalizeStoredString(attachment.caption_status) || null,
        index_status: normalizeStoredString(attachment.index_status) || null,
        placeholder_caption:
          typeof attachment.placeholder_caption === "boolean"
            ? attachment.placeholder_caption
            : null,
        uploading: false,
      };
    })
    .filter(Boolean)
    .slice(0, MAX_STORED_COMPOSER_ATTACHMENTS);
};

const readStoredComposerDraft = (sessionId) => {
  const storage = getComposerDraftStorage();
  if (!storage) return { message: "", attachments: [], visionWorkflow: "auto" };
  try {
    const raw = storage.getItem(getComposerDraftStorageKey(sessionId));
    if (!raw) return { message: "", attachments: [], visionWorkflow: "auto" };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return { message: "", attachments: [], visionWorkflow: "auto" };
    }
    const tombstonedAttachmentIds =
      readStoredComposerAttachmentTombstones(sessionId);
    const attachments = serializeComposerDraftAttachments(parsed.attachments).filter(
      (attachment) =>
        !storedComposerAttachmentKeys(attachment).some((key) =>
          tombstonedAttachmentIds.has(key),
        ),
    );
    return {
      message: typeof parsed.message === "string" ? parsed.message : "",
      attachments,
      visionWorkflow: normalizeVisionWorkflow(parsed.visionWorkflow),
    };
  } catch {
    return { message: "", attachments: [], visionWorkflow: "auto" };
  }
};

const writeStoredComposerDraft = (sessionId, draft = {}) => {
  const storage = getComposerDraftStorage();
  if (!storage) return;
  const message = typeof draft.message === "string" ? draft.message : "";
  const attachments = serializeComposerDraftAttachments(draft.attachments);
  const visionWorkflow = normalizeVisionWorkflow(draft.visionWorkflow);
  const hasDraft = Boolean(message.trim()) || attachments.length > 0;
  const key = getComposerDraftStorageKey(sessionId);
  try {
    if (!hasDraft) {
      storage.removeItem(key);
      return;
    }
    storage.setItem(
      key,
      JSON.stringify({
        message,
        attachments,
        visionWorkflow,
        updatedAt: new Date().toISOString(),
      }),
    );
  } catch {
    // Storage quota/privacy failures should never block chat composition.
  }
};

const mergeStoredComposerDraftAttachment = (sessionId, attachment) => {
  const [serialized] = serializeComposerDraftAttachments([attachment]);
  if (!serialized) return;
  const draft = readStoredComposerDraft(sessionId);
  const keyFor = (item) =>
    item?.content_hash || item?.contentHash || item?.remoteUrl || item?.url || item?.name;
  const nextKey = keyFor(serialized);
  const attachments = (draft.attachments || []).filter(
    (item) => keyFor(item) !== nextKey,
  );
  attachments.push(serialized);
  writeStoredComposerDraft(sessionId, {
    ...draft,
    attachments,
  });
};

const storedComposerAttachmentKeys = (attachment) =>
  [
    attachment?.outboxId,
    attachment?.outbox_id,
    attachment?.contentHash,
    attachment?.content_hash,
    attachment?.remoteUrl,
    attachment?.url,
    attachment?.id,
  ]
    .map(normalizeStoredString)
    .filter(Boolean);

const removeStoredComposerDraftAttachments = (sessionId, attachments = []) => {
  const submittedKeys = new Set(
    attachments.flatMap((attachment) => storedComposerAttachmentKeys(attachment)),
  );
  if (!submittedKeys.size) return;
  const draft = readStoredComposerDraft(sessionId);
  const remainingAttachments = (draft.attachments || []).filter(
    (attachment) =>
      !storedComposerAttachmentKeys(attachment).some((key) =>
        submittedKeys.has(key),
      ),
  );
  if (remainingAttachments.length === (draft.attachments || []).length) return;
  writeStoredComposerDraft(sessionId, {
    ...draft,
    attachments: remainingAttachments,
  });
};

const fileFromAttachmentOutboxEntry = (entry) => {
  const storedFile = entry?.file;
  if (!storedFile) return null;
  if (typeof File !== "undefined" && storedFile instanceof File) {
    return storedFile;
  }
  if (typeof Blob === "undefined" || !(storedFile instanceof Blob)) {
    return null;
  }
  if (typeof File === "undefined") return storedFile;
  return new File([storedFile], entry.name || "attachment", {
    type: entry.type || storedFile.type || "application/octet-stream",
    lastModified: Number(entry.lastModified) || Date.now(),
  });
};

const createAttachmentPreviewUrl = (file) => {
  if (!file || typeof URL === "undefined" || !URL.createObjectURL) return "";
  try {
    return URL.createObjectURL(file);
  } catch {
    return "";
  }
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

const resolveTtsRoute = (modelValue, voiceValue, response = {}) => {
  const model = String(response?.model || modelValue || DEFAULT_TTS_MODEL).trim();
  const voice = String(response?.voice || voiceValue || DEFAULT_TTS_VOICE).trim();
  const provider = String(response?.provider || "").trim().toLowerCase();
  const modelLower = model.toLowerCase();
  const isOpenAiTtsModel =
    modelLower.startsWith("tts-") ||
    (modelLower.startsWith("gpt-4o") && modelLower.includes("tts"));
  const route =
    provider === "openai" || isOpenAiTtsModel
      ? "API"
      : provider === "local" || model
        ? "Local"
        : "Default";
  const providerLabel =
    provider === "openai"
      ? "OpenAI"
      : provider === "local"
        ? "local engine"
        : route === "API"
          ? "OpenAI"
          : route === "Local"
            ? "local engine"
            : "server default";
  const parts = [`Text-to-speech route: ${route} (${providerLabel})`];
  if (model) parts.push(`model: ${model}`);
  if (voice) parts.push(`voice: ${voice}`);
  return {
    route,
    provider: provider || providerLabel,
    model,
    voice,
    tooltip: parts.join(" | "),
  };
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

const comparableToolArgValue = (value) => {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return stableValue(value);
  }
  if (Array.isArray(value)) {
    const normalized = value
      .map((item) => comparableToolArgValue(item))
      .filter((item) => item !== null && typeof item !== "undefined");
    return normalized.length ? normalized : null;
  }
  if (value && typeof value === "object") {
    const namedValue = value.id || value.name || value.title || value.key || null;
    if (
      typeof namedValue === "string" ||
      typeof namedValue === "number" ||
      typeof namedValue === "boolean"
    ) {
      return stableValue(namedValue);
    }
    return null;
  }
  return null;
};

const toolArgsSemanticallyMatch = (leftArgs = {}, rightArgs = {}) => {
  const leftKeys = Object.keys(leftArgs);
  const rightKeys = Object.keys(rightArgs);
  if (!leftKeys.length || !rightKeys.length) {
    return leftKeys.length === rightKeys.length;
  }
  const [smaller, larger] =
    leftKeys.length <= rightKeys.length
      ? [leftArgs, rightArgs]
      : [rightArgs, leftArgs];
  let matched = 0;
  for (const key of Object.keys(smaller)) {
    const smallerValue = comparableToolArgValue(smaller[key]);
    if (smallerValue === null || typeof smallerValue === "undefined") {
      continue;
    }
    const largerValue = comparableToolArgValue(larger[key]);
    if (largerValue === null || typeof largerValue === "undefined") {
      continue;
    }
    if (JSON.stringify(smallerValue) !== JSON.stringify(largerValue)) {
      return false;
    }
    matched += 1;
  }
  return matched > 0;
};

const toolsSemanticallyMatch = (left, right) => {
  const leftName = typeof left?.name === "string" ? left.name.trim().toLowerCase() : "";
  const rightName =
    typeof right?.name === "string" ? right.name.trim().toLowerCase() : "";
  if (!leftName || leftName !== rightName) return false;
  const leftArgs =
    left?.args && typeof left.args === "object" && !Array.isArray(left.args) ? left.args : {};
  const rightArgs =
    right?.args && typeof right.args === "object" && !Array.isArray(right.args)
      ? right.args
      : {};
  return toolArgsSemanticallyMatch(leftArgs, rightArgs);
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
    if (normalized.error && Array.isArray(normalized.suggestions)) {
      const suggestions = normalized.suggestions
        .slice(0, 4)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .join(", ");
      return suggestions
        ? `${String(normalized.error).replace(/_/g, " ")}; suggestions: ${suggestions}`
        : String(normalized.error).replace(/_/g, " ");
    }
    if (Array.isArray(normalized.suggestions) && normalized.suggestions.length) {
      return `suggestions: ${normalized.suggestions
        .slice(0, 4)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .join(", ")}`;
    }
    if (Array.isArray(normalized.matches)) {
      const count = normalized.matches.length;
      const first = normalized.matches.find((match) => match && typeof match === "object");
      const snippet = first?.snippet || first?.source || first?.title || "";
      return snippet ? `${count} match${count === 1 ? "" : "es"}: ${snippet}` : `${count} matches`;
    }
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
    if (base.some((existing) => toolsSemanticallyMatch(existing, entry))) return;
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
    if (
      idx === -1 &&
      !toolId &&
      normalizeToolStatus(normalized.status) === "proposed"
    ) {
      idx = merged.findIndex((entry) => {
        const existingId = entry?.id || entry?.request_id || null;
        if (existingId) return toolsSemanticallyMatch(entry, normalized);
        return (
          normalizeToolStatus(entry?.status) !== "proposed" &&
          toolsSemanticallyMatch(entry, normalized)
        );
      });
    }
    if (idx >= 0) {
      merged[idx] = { ...merged[idx], ...normalized };
    } else {
      merged.push(normalized);
    }
  });
  return merged;
};

const readSubchatControlPayload = (value, depth = 0) => {
  if (!value || typeof value !== "object" || depth > 4) return null;
  const control =
    value.control && typeof value.control === "object" ? value.control : null;
  if (control) {
    const action = String(control.action || "").trim();
    const kind = String(control.kind || "").trim();
    if (kind === "subchat_control" || action === "return_to_parent" || action === "continue") {
      const requestedMinutes = Number(
        control.requested_minutes ?? control.requestedMinutes ?? value.requested_minutes ?? 0,
      );
      return {
        action,
        kind: kind || "subchat_control",
        parentSessionId: String(
          control.parent_session_id ||
            control.parentSessionId ||
            value.parent_session_id ||
            value.parentSessionId ||
            "",
        ).trim(),
        parentMessageId: String(
          control.parent_message_id ||
            control.parentMessageId ||
            value.parent_message_id ||
            value.parentMessageId ||
            "",
        ).trim(),
        requestedMinutes: Number.isFinite(requestedMinutes)
          ? requestedMinutes
          : 0,
      };
    }
  }
  if (value.data && typeof value.data === "object") {
    const nested = readSubchatControlPayload(value.data, depth + 1);
    if (nested) return nested;
  }
  if (value.result && typeof value.result === "object") {
    const nested = readSubchatControlPayload(value.result, depth + 1);
    if (nested) return nested;
  }
  return null;
};

export const resolveSubchatControlFromTools = (tools) => {
  if (!Array.isArray(tools)) return null;
  for (const rawTool of tools) {
    const tool = normalizeToolEntry(rawTool) || rawTool;
    if (!tool || typeof tool !== "object") continue;
    const name = String(tool.name || "").trim().toLowerCase();
    if (name && name !== "subchat") continue;
    const control = readSubchatControlPayload(tool.result || tool);
    if (control) return control;
  }
  return null;
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

const isActionableToolStatus = (tool) => {
  const status = getEffectiveToolStatus(tool) || normalizeToolStatus(tool?.status);
  return status === "proposed" || status === "pending";
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

const formatRuntimeSourceLabel = (mode, model, provider = "") => {
  const safeMode = typeof mode === "string" ? mode.trim() : "";
  const safeModel = typeof model === "string" ? model.trim() : "";
  const safeProvider = typeof provider === "string" ? provider.trim() : "";
  if (safeMode && safeModel) {
    const normalizedMode = safeMode.toLowerCase();
    const normalizedModel = safeModel.toLowerCase();
    const normalizedProvider = safeProvider.toLowerCase();
    if (
      normalizedMode === "local" &&
      normalizedProvider &&
      normalizedProvider !== normalizedModel
    ) {
      return `${safeMode}/${safeProvider}:${safeModel}`;
    }
    return `${safeMode}:${safeModel}`;
  }
  return formatModelSourceLabel(safeMode, safeModel || safeProvider);
};

const resolveLiveStreamSourceLabel = (metadata = {}) => {
  const live =
    metadata && typeof metadata === "object" && metadata.live_stream && typeof metadata.live_stream === "object"
      ? metadata.live_stream
      : null;
  if (!live) return "";
  const mode = typeof live.mode === "string" ? live.mode.trim() : "";
  const model = typeof live.model === "string" ? live.model.trim() : "";
  const providerCandidates = [live.provider, live.transport, live.source];
  const provider =
    providerCandidates.find((value) => typeof value === "string" && value.trim()) || "";
  const base = formatRuntimeSourceLabel(mode, model || provider, provider);
  if (!base) return "";
  if (mode) return `live/${base}`;
  return `live:${base}`;
};

const resolveLiveSessionRuntime = (session, fallbackMode = "", fallbackModel = "") => {
  const runtime = session?.runtime && typeof session.runtime === "object" ? session.runtime : {};
  const providerCandidates = [
    session?.provider,
    session?.transport,
    session?.backend,
    session?.source,
    runtime?.provider,
  ];
  const provider =
    providerCandidates.find((value) => typeof value === "string" && value.trim()) || "";
  const modeCandidates = [
    runtime?.mode,
    runtime?.response_mode,
    session?.mode,
    session?.runtime_mode,
    session?.target_mode,
    provider === "openai-realtime" ? "api" : fallbackMode,
  ];
  const modelCandidates = [
    runtime?.response_model,
    session?.model,
    session?.response_model,
    session?.target_model,
    session?.session?.model,
    fallbackModel,
  ];
  const multimodalCandidates = [
    runtime?.multimodal_model,
    session?.multimodal_model,
  ];
  const voiceCandidates = [
    runtime?.voice_model,
    session?.voice,
    session?.session?.audio?.output?.voice,
  ];
  const sourceCandidates = [
    runtime?.source,
    session?.source,
    session?.provider,
    session?.transport,
    "live",
  ];
  return {
    source:
      sourceCandidates.find((value) => typeof value === "string" && value.trim()) || "live",
    transport:
      (typeof session?.transport === "string" && session.transport.trim()) ||
      (typeof session?.provider === "string" && session.provider.trim()) ||
      (typeof runtime?.transport_backend === "string" && runtime.transport_backend.trim()) ||
      "",
    provider: typeof provider === "string" ? provider.trim() : "",
    mode:
      modeCandidates.find((value) => typeof value === "string" && value.trim()) || "",
    model:
      modelCandidates.find((value) => typeof value === "string" && value.trim()) || "",
    multimodal_model:
      multimodalCandidates.find((value) => typeof value === "string" && value.trim()) || "",
    voice:
      voiceCandidates.find((value) => typeof value === "string" && value.trim()) || "",
  };
};

const createRealtimeResponseLifecycleState = () => ({
  active: false,
  requested: false,
  queued: false,
});

const resolveModeModel = (mode, state) => {
  const currentMode = (mode || state.backendMode || "").toLowerCase();
  return {
    mode: currentMode || state.backendMode,
    model: resolveRequestModelForMode({
      backendMode: currentMode || state.backendMode,
      apiModel: state.apiModel,
      transformerModel: state.transformerModel,
      localModel: state.localModel,
    }),
  };
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
  if (meta.tool_response_pending) {
    const tools = resolveMessageTools(msg);
    const readyToContinue = Boolean(buildToolContinuationBatch(tools));
    return {
      label: readyToContinue ? "tool done" : "using tool",
      tone: readyToContinue ? "warn" : "pending",
      title: readyToContinue
        ? "Tool results are ready; continue generation to finish the answer."
        : "Float is waiting on a tool call before continuing the answer.",
    };
  }
  if (meta.output_truncated) {
    return {
      label: "token cap",
      tone: "warn",
      title: "The provider stopped after reaching the configured output-token budget.",
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

const compactMetadataValue = (value, limit = 96) => {
  if (value === null || typeof value === "undefined") return "";
  let text = "";
  if (typeof value === "string") {
    text = value.trim();
  } else if (typeof value === "number" || typeof value === "boolean") {
    text = String(value);
  } else if (Array.isArray(value)) {
    text = value.map((item) => compactMetadataValue(item, 40)).filter(Boolean).join(", ");
  } else if (typeof value === "object") {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3)).trim()}...` : text;
};

const buildMessageMetadataRows = (
  msg,
  { sourceLabel = "", toolCount = 0, ragCount = 0 } = {},
) => {
  if (!msg || typeof msg !== "object") return [];
  const metadata = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
  const workflow =
    metadata.workflow && typeof metadata.workflow === "object"
      ? metadata.workflow.name
      : metadata.workflow;
  const usage =
    metadata.usage && typeof metadata.usage === "object"
      ? [
          metadata.usage.prompt_tokens ? `in ${metadata.usage.prompt_tokens}` : "",
          metadata.usage.completion_tokens ? `out ${metadata.usage.completion_tokens}` : "",
          metadata.usage.total_tokens ? `total ${metadata.usage.total_tokens}` : "",
        ]
          .filter(Boolean)
          .join(" / ")
      : "";
  const usageLabel =
    String(metadata.usage?.source || "").trim().toLowerCase() === "estimate"
      ? "Usage (estimated)"
      : "Usage";
  const reasoning =
    metadata.reasoning && typeof metadata.reasoning === "object"
      ? metadata.reasoning
      : null;
  const generation =
    metadata.generation && typeof metadata.generation === "object"
      ? metadata.generation
      : null;
  const effectiveReasoningEffort = reasoning?.effective_effort;
  const reasoningPrimary =
    typeof effectiveReasoningEffort === "number"
      ? `${reasoning.preset || "custom"} · ${effectiveReasoningEffort.toFixed(2)}`
      : reasoning?.preset || effectiveReasoningEffort;
  const reasoningLabel = reasoning
    ? [
        reasoningPrimary,
        reasoning.rounded ? `rounded from ${reasoning.requested_effort}` : "",
      ]
        .filter(Boolean)
        .join(" / ")
    : "";
  return [
    { label: "Status", value: metadata.status },
    { label: "Source", value: sourceLabel },
    { label: "Mode", value: metadata.mode },
    { label: "Model", value: metadata.model || metadata.model_received || metadata.model_requested },
    { label: "Workflow", value: workflow },
    { label: "Reasoning", value: reasoningLabel },
    {
      label: "Response limit",
      value: generation?.max_output_tokens
        ? `${generation.max_output_tokens} tokens`
        : generation?.output_limit_source === "provider_default"
          ? "provider default"
          : reasoning?.output_token_budget
            ? `${reasoning.output_token_budget} tokens`
            : "",
    },
    { label: "Finish", value: metadata.finish_reason },
    {
      label: "Termination",
      value: metadata.termination_category || metadata.category,
    },
    { label: "Tools", value: toolCount ? `${toolCount}` : "" },
    { label: "Context", value: ragCount ? `${ragCount}` : "" },
    { label: "Response", value: compactMetadataValue(metadata.response_id, 42) },
    { label: "Request", value: compactMetadataValue(metadata.request_id, 42) },
    { label: usageLabel, value: usage },
  ].filter((row) => compactMetadataValue(row.value));
};

const Chat = ({
    thoughts = [],
    activeMessageId,
    setActiveMessageId,
    messageDelta,
    streamActivity = null,
    onOpenConsole,
    onOpenConversation,
    parentConversationLink = null,
    subchatLinksByMessage = {},
  }) => {
  const globalContext = useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const navigate = useNavigate();
  const initialComposerDraftRef = useRef(undefined);
  if (initialComposerDraftRef.current === undefined) {
    initialComposerDraftRef.current = readStoredComposerDraft(state.sessionId);
  }
  const composerDraftSessionRef = useRef(state.sessionId);
  const activeComposerSessionRef = useRef(state.sessionId);
  activeComposerSessionRef.current = state.sessionId;
  const initialComposerDraft = initialComposerDraftRef.current || {};
  const [message, setMessage] = useState(() => initialComposerDraft.message || "");
  const [composerCursor, setComposerCursor] = useState(() =>
    (initialComposerDraft.message || "").length,
  );
  const [commandSuggestions, setCommandSuggestions] = useState([]);
  const [commandSuggestionsLoading, setCommandSuggestionsLoading] = useState(false);
  const [activeCommandSuggestionIndex, setActiveCommandSuggestionIndex] = useState(0);
  const [commandMenuStyle, setCommandMenuStyle] = useState(null);
  const [loading, setLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [regeneratingMessageId, setRegeneratingMessageId] = useState(null);
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null);
  // attachments: [{ id, file, url }]
  const [attachments, setAttachments] = useState(() =>
    Array.isArray(initialComposerDraft.attachments)
      ? initialComposerDraft.attachments
      : [],
  );
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;
  const [visionWorkflow, setVisionWorkflow] = useState(() =>
    normalizeVisionWorkflow(initialComposerDraft.visionWorkflow),
  );
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [screenCaptureBusy, setScreenCaptureBusy] = useState(false);
  const [liveVisualMode, setLiveVisualMode] = useState("off");
  const [liveVisualError, setLiveVisualError] = useState("");
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [chatSettingsSection, setChatSettingsSection] = useState("camera");
  const [chatWorkflowProfiles, setChatWorkflowProfiles] = useState(
    FALLBACK_WORKFLOW_PROFILES,
  );
  const [workflowCatalogResolved, setWorkflowCatalogResolved] = useState(false);
  const [availableInputDevices, setAvailableInputDevices] = useState({
    audioinput: [],
    videoinput: [],
  });
  const [micTestActive, setMicTestActive] = useState(false);
  const [micTestLevel, setMicTestLevel] = useState(0);
  const [chatSettingsPopoverStyle, setChatSettingsPopoverStyle] = useState(null);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [attachmentPopoverStyle, setAttachmentPopoverStyle] = useState(null);
  const [inputAlertStyle, setInputAlertStyle] = useState(null);
  const inputAlerts = [error, cameraError, liveVisualError].filter(
    (value) => typeof value === "string" && value.trim(),
  );
  const inputAlertsKey = inputAlerts.join("\n");
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
  const realtimeToolNameMapRef = useRef({});
  const realtimePendingToolCallsRef = useRef(new Set());
  const realtimeConfiguredToolsRef = useRef(false);
  const chatSettingsMenuRef = useRef(null);
  const chatSettingsTriggerRef = useRef(null);
  const chatSettingsPopoverRef = useRef(null);
  const attachmentMenuRef = useRef(null);
  const attachmentTriggerRef = useRef(null);
  const attachmentPopoverRef = useRef(null);
  const inputAlertStackRef = useRef(null);
  const removedAttachmentIdsRef = useRef(new Set());
  const activeAttachmentUploadsRef = useRef(new Set());
  const toolCatalogRef = useRef(null);
  const knowledgeDocsRef = useRef(null);
  const commandLookupRequestRef = useRef(0);
  const inputMainRef = useRef(null);
  const activeCommandOptionRef = useRef(null);
  const realtimeResponseLifecycleRef = useRef(createRealtimeResponseLifecycleState());
  const realtimeToolSetupPromiseRef = useRef(null);
  const realtimeTurnDetectionRef = useRef({
    type: "server_vad",
    interrupt_response: true,
  });
  const realtimeTranscriptionModelRef = useRef("gpt-realtime-whisper");
  const micTestRef = useRef({
    rawStream: null,
    processedStream: null,
    audioContext: null,
    analyser: null,
    rafId: null,
  });
  const chatContainerRef = useRef(null);
  const chatBoxRef = useRef(null);
  const inputBoxRef = useRef(null);
  const composerInputRef = useRef(null);
  const bottomSentinelRef = useRef(null);
  const messageRefs = useRef({});
  const roomRef = useRef(null);
  const peerConnectionRef = useRef(null);
  const voiceChannelRef = useRef(null);
  const voiceStreamRef = useRef(null);
  const liveSessionAttemptRef = useRef(0);
  const remoteAudioRef = useRef(null);
  const liveStreamStateRef = useRef({ sessionId: null, currentTurn: null, runtime: null });
  const clickInlineToolReviewButton = useCallback((target, action) => {
    const root = chatContainerRef.current;
    if (!root || typeof root.querySelector !== "function") return false;
    const actionSelector = `.tool-action-btn.${action}`;
    const scopes = toolReviewScopeSelectors(target);
    for (const scope of scopes) {
      let button = null;
      try {
        button = root.querySelector(`${scope} ${actionSelector}`);
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
  }, []);
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
    route: null,
  });
  const ttsAudioRef = useRef(null);
  const ttsRequestIdRef = useRef(0);
  const [collapsedTools, setCollapsedTools] = useState({});
  const [expandedToolCards, setExpandedToolCards] = useState({});
  const [collapseAllTools, setCollapseAllTools] = useState(true);
  const thinkingMode = normalizeThinkingMode(state.thinkingMode);
  const thinkingEffortValue = reasoningEffortValue(thinkingMode);
  const customThinkingEffort = isCustomReasoningEffort(thinkingMode);
  const outputTokenMode = normalizeOutputTokenMode(state.outputTokenMode);
  const customOutputTokens = normalizeCustomOutputTokens(
    state.customOutputTokens,
  );
  const outputTokenLimit = selectedOutputTokenLimit(
    outputTokenMode,
    customOutputTokens,
  );
  const requestedWorkflowProfile = state.workflowProfile || "default";
  const workflowProfile = resolveSelectableWorkflowId(
    chatWorkflowProfiles,
    requestedWorkflowProfile,
    { allowUnknown: !workflowCatalogResolved },
  );
  const selectableChatWorkflowProfiles = chatWorkflowProfiles.filter(
    isWorkflowSelectableInChat,
  );
  const workflowOptions = selectableChatWorkflowProfiles.some(
    (profile) => profile.id === workflowProfile,
  )
    ? selectableChatWorkflowProfiles
    : [
        {
          id: workflowProfile,
          label: workflowProfile,
          description: "Current profile from saved settings.",
        },
        ...selectableChatWorkflowProfiles,
      ];
  const activeWorkflowProfile =
    workflowOptions.find((profile) => profile.id === workflowProfile) ||
    workflowOptions[0] ||
    null;
  useEffect(() => {
    const knownProfile = chatWorkflowProfiles.some(
      (profile) => profile.id === requestedWorkflowProfile,
    );
    if (
      requestedWorkflowProfile === workflowProfile ||
      (!workflowCatalogResolved && !knownProfile)
    ) {
      return;
    }
    setState((previous) => ({
      ...previous,
      workflowProfile,
    }));
  }, [
    chatWorkflowProfiles,
    requestedWorkflowProfile,
    setState,
    workflowCatalogResolved,
    workflowProfile,
  ]);
  const textRagEnabled = state.textRagEnabled !== false;
  const visionRagEnabled = state.visionRagEnabled !== false;
  const ragEmbeddingModel = state.ragEmbeddingModel || "local:all-MiniLM-L6-v2";
  const ragClipModel = state.ragClipModel || "ViT-B-32";
  const preferredMicDeviceId = String(state.preferredMicDeviceId || "");
  const preferredCameraDeviceId = String(state.preferredCameraDeviceId || "");
  const micInputGain = clamp(Number(state.micInputGain) || 1, 0.25, 2);
  const outputVolume = clamp(Number(state.outputVolume) || 1, 0, 1.5);
  const liveCameraDefaultEnabled = state.liveCameraDefaultEnabled === true;
  const selectedSttModel = String(state.sttModel || "gpt-realtime-whisper").trim();
  const selectedTtsModel = String(state.ttsModel || "tts-1").trim();
  const selectedTtsVoice = String(state.voiceModel || "").trim();
  const selectedTtsVoiceOptions = useMemo(
    () => voiceOptionsForTtsModel(selectedTtsModel),
    [selectedTtsModel],
  );
  const configuredTtsRoute = useMemo(
    () => resolveTtsRoute(state.ttsModel, state.voiceModel),
    [state.ttsModel, state.voiceModel],
  );
  const thinkingPayload = thinkingPayloadForMode(thinkingMode);
  const outputTokensPayload = outputTokenPayload(
    outputTokenMode,
    customOutputTokens,
  );
  const generationControlPayload = {
    ...thinkingPayload,
    ...outputTokensPayload,
  };
  const ragPayload = {
    use_rag: textRagEnabled || visionRagEnabled,
    use_text_rag: textRagEnabled,
    use_vision_rag: visionRagEnabled,
  };
  const workflowPayload = {
    workflow: workflowProfile,
    modules: Array.isArray(state.enabledWorkflowModules)
      ? state.enabledWorkflowModules
      : [],
  };
  const composerCommandContext = useMemo(
    () => getCommandCompletionContext(message, composerCursor),
    [message, composerCursor],
  );
  const activeCommandSuggestion =
    activeCommandSuggestionIndex >= 0 &&
    activeCommandSuggestionIndex < commandSuggestions.length
      ? commandSuggestions[activeCommandSuggestionIndex]
      : null;
  const setThinkingMode = useCallback((mode) => {
    const normalized = normalizeThinkingMode(mode);
    setState((prev) => {
      if (normalizeThinkingMode(prev.thinkingMode) === normalized) return prev;
      return { ...prev, thinkingMode: normalized };
    });
  }, [setState]);
  const setOutputTokenMode = useCallback((mode) => {
    const normalized = normalizeOutputTokenMode(mode);
    setState((prev) => {
      if (normalizeOutputTokenMode(prev.outputTokenMode) === normalized) return prev;
      return { ...prev, outputTokenMode: normalized };
    });
  }, [setState]);
  const setCustomOutputTokens = useCallback((value) => {
    const normalized = normalizeCustomOutputTokens(value);
    setState((prev) => {
      if (normalizeCustomOutputTokens(prev.customOutputTokens) === normalized) {
        return prev;
      }
      return { ...prev, customOutputTokens: normalized };
    });
  }, [setState]);
  const setRagEnabled = useCallback((field, enabled) => {
    setState((prev) => {
      const nextValue = Boolean(enabled);
      if (prev[field] === nextValue) return prev;
      return { ...prev, [field]: nextValue };
    });
  }, [setState]);
  const setRagModel = useCallback((field, value) => {
    const nextValue = String(value || "").trim();
    const stateKey = field === "rag_clip_model" ? "ragClipModel" : "ragEmbeddingModel";
    const fallback = field === "rag_clip_model" ? "ViT-B-32" : "local:all-MiniLM-L6-v2";
    const normalizedValue = nextValue || fallback;
    setState((prev) => {
      if (prev[stateKey] === normalizedValue) return prev;
      return { ...prev, [stateKey]: normalizedValue };
    });
    axios.post("/api/settings", { [field]: normalizedValue }).catch((err) => {
      console.error("RAG model setting failed", err);
      setError(getRequestErrorDetail(err, "RAG model setting failed."));
    });
  }, [setError, setState]);
  const setWorkflowProfile = useCallback((workflow) => {
    const normalized = String(workflow || "").trim() || "default";
    setState((prev) => {
      if ((prev.workflowProfile || "default") === normalized) return prev;
      return { ...prev, workflowProfile: normalized };
    });
  }, [setState]);
  const openWorkflowSettings = useCallback(() => {
    setChatSettingsOpen(false);
    navigate("/knowledge?tab=skills&view=workflows");
  }, [navigate]);
  const placeComposerSelection = useCallback((nextText, nextCursor) => {
    const safeText = typeof nextText === "string" ? nextText : "";
    const safeCursor = clampCursor(nextCursor, safeText.length);
    setMessage(safeText);
    setComposerCursor(safeCursor);
    if (typeof window !== "undefined") {
      window.requestAnimationFrame(() => {
        const target = composerInputRef.current;
        if (
          target &&
          typeof target.setSelectionRange === "function" &&
          document.activeElement === target
        ) {
          target.setSelectionRange(safeCursor, safeCursor);
        }
      });
    }
  }, []);
  const syncComposerCursorFromTarget = useCallback((target) => {
    if (!target || typeof target.selectionStart !== "number") return;
    setComposerCursor(clampCursor(target.selectionStart, String(target.value || "").length));
  }, []);
  const handleComposerChange = useCallback(
    (event) => {
      const nextValue = typeof event?.target?.value === "string" ? event.target.value : "";
      setMessage(nextValue);
      syncComposerCursorFromTarget(event?.target);
    },
    [syncComposerCursorFromTarget],
  );
  const handleComposerSelectionChange = useCallback(
    (event) => {
      syncComposerCursorFromTarget(event?.target);
    },
    [syncComposerCursorFromTarget],
  );
  const loadToolCatalog = useCallback(async () => {
    if (Array.isArray(toolCatalogRef.current)) return toolCatalogRef.current;
    const res = await axios.get("/api/tools/catalog");
    const tools = Array.isArray(res.data?.tools) ? res.data.tools : [];
    toolCatalogRef.current = tools;
    return tools;
  }, []);
  const loadKnowledgeDocs = useCallback(async () => {
    if (Array.isArray(knowledgeDocsRef.current)) return knowledgeDocsRef.current;
    const res = await axios.get("/api/knowledge/list");
    const ids = Array.isArray(res.data?.ids) ? res.data.ids : [];
    const metadatas = Array.isArray(res.data?.metadatas) ? res.data.metadatas : [];
    const docs = ids.map((id, index) => {
      const meta = metadatas[index] && typeof metadatas[index] === "object" ? metadatas[index] : {};
      const source = String(meta.relative_path || meta.source || meta.filename || id || "").trim();
      const title = String(meta.title || meta.filename || source || id || "").trim();
      return {
        id,
        title,
        source,
      };
    });
    knowledgeDocsRef.current = docs;
    return docs;
  }, []);
  useEffect(() => {
    let cancelled = false;
    const requestId = commandLookupRequestRef.current + 1;
    commandLookupRequestRef.current = requestId;
    const loadSuggestions = async () => {
      if (!composerCommandContext) {
        setCommandSuggestions([]);
        setCommandSuggestionsLoading(false);
        setActiveCommandSuggestionIndex(0);
        return;
      }
      setCommandSuggestionsLoading(true);
      try {
        const query = String(composerCommandContext.query || "").trim().toLowerCase();
        let suggestions = [];
        if (composerCommandContext.kind === "tool") {
          const tools = await loadToolCatalog();
          suggestions = tools
            .map((tool) => ({
              kind: "tool",
              label: String(tool?.name || "").trim(),
              description: String(tool?.summary || tool?.description || "").trim(),
              insertText: buildCommandInsertText("tool", tool?.name || ""),
            }))
            .filter((tool) => tool.label)
            .filter((tool) => !query || tool.label.toLowerCase().includes(query))
            .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
        } else {
          const buildFileSuggestions = async () => {
            const docs = await loadKnowledgeDocs();
            return docs
              .filter((doc) => doc && doc.source)
              .filter((doc) => {
                if (!query) return true;
                const haystack = [doc.source, doc.title, doc.id]
                  .filter(Boolean)
                  .join(" ")
                  .toLowerCase();
                return haystack.includes(query);
              })
              .map((doc) => ({
                kind: "file",
                label: doc.source,
                description: doc.title && doc.title !== doc.source ? doc.title : "knowledge file",
                insertText: buildCommandInsertText("file", doc.source),
              }));
          };
          const buildMemorySuggestions = async () => {
            if (query) {
              const res = await axios.post("/api/memory/search", {
                query,
                limit: COMMAND_COMPLETION_LIMIT * 2,
              });
              const results = Array.isArray(res.data?.results) ? res.data.results : [];
              return results.map((item) => ({
                kind: "memory",
                label: String(item?.key || "").trim(),
                description: String(item?.snippet || "").trim(),
                insertText: buildCommandInsertText("memory", item?.key || ""),
              }));
            }
            const res = await axios.get("/api/memory", {
              params: { detailed: true },
            });
            const items = Array.isArray(res.data?.items) ? res.data.items : [];
            return items.map((item) => ({
              kind: "memory",
              label: String(item?.key || "").trim(),
              description: String(item?.hint || item?.value || "").trim(),
              insertText: buildCommandInsertText("memory", item?.key || ""),
            }));
          };
          if (composerCommandContext.kind === "file") {
            suggestions = await buildFileSuggestions();
          } else if (composerCommandContext.kind === "memory") {
            suggestions = await buildMemorySuggestions();
          } else {
            const [files, memories] = await Promise.all([
              buildFileSuggestions(),
              buildMemorySuggestions(),
            ]);
            suggestions = [...files, ...memories];
          }
          suggestions = suggestions
            .filter((item) => item.label)
            .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
        }
        if (cancelled || requestId !== commandLookupRequestRef.current) return;
        setCommandSuggestions(suggestions.slice(0, COMMAND_COMPLETION_LIMIT));
        setActiveCommandSuggestionIndex(0);
      } catch (err) {
        if (cancelled || requestId !== commandLookupRequestRef.current) return;
        setCommandSuggestions([]);
      } finally {
        if (!cancelled && requestId === commandLookupRequestRef.current) {
          setCommandSuggestionsLoading(false);
        }
      }
    };
    void loadSuggestions();
    return () => {
      cancelled = true;
    };
  }, [composerCommandContext, loadKnowledgeDocs, loadToolCatalog]);

  const updateCommandMenuPosition = useCallback(() => {
    if (
      !composerCommandContext ||
      (!commandSuggestionsLoading && commandSuggestions.length === 0) ||
      typeof window === "undefined"
    ) {
      setCommandMenuStyle(null);
      return;
    }
    const anchor = inputMainRef.current;
    if (!anchor || typeof anchor.getBoundingClientRect !== "function") {
      setCommandMenuStyle(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const inputBox = anchor.closest?.(".input-box");
    const inputBoxStyles =
      inputBox && typeof window.getComputedStyle === "function"
        ? window.getComputedStyle(inputBox)
        : null;
    const railWidth =
      parseFloat(inputBoxStyles?.getPropertyValue("--input-action-rail-width") || "0") || 0;
    const width = Math.max(220, Math.round(rect.width - railWidth - 12));
    setCommandMenuStyle({
      left: `${Math.round(rect.left)}px`,
      top: `${Math.round(rect.top - 10)}px`,
      width: `${width}px`,
    });
  }, [
    commandSuggestions.length,
    commandSuggestionsLoading,
    composerCommandContext,
  ]);

  useEffect(() => {
    updateCommandMenuPosition();
  }, [updateCommandMenuPosition]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    const frame = window.requestAnimationFrame(() => {
      const activeNode = activeCommandOptionRef.current;
      if (!activeNode || typeof activeNode.scrollIntoView !== "function") {
        return;
      }
      activeNode.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeCommandSuggestionIndex, commandSuggestions.length]);

  useEffect(() => {
    if (!composerCommandContext) return undefined;
    const handlePositionUpdate = () => {
      updateCommandMenuPosition();
    };
    window.addEventListener("resize", handlePositionUpdate);
    window.addEventListener("scroll", handlePositionUpdate, true);
    return () => {
      window.removeEventListener("resize", handlePositionUpdate);
      window.removeEventListener("scroll", handlePositionUpdate, true);
    };
  }, [composerCommandContext, updateCommandMenuPosition]);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const activeRequestRef = useRef(null);
  const activeRequestActivityRef = useRef(null);
  const [toolEditorState, setToolEditorState] = useState(null); // { tool, onSubmit }
  const [messageEditorState, setMessageEditorState] = useState(null); // { mode: "user"|"assistant", assistantId, text }
  const [audioTranscribing, setAudioTranscribing] = useState(false);
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
  const [isChatWindowResizing, setIsChatWindowResizing] = useState(false);
  const [compactionSummaryMode, setCompactionSummaryMode] = useState("deterministic");
  const [compactionBusy, setCompactionBusy] = useState(false);
  const [compactionPreview, setCompactionPreview] = useState(null);
  const [compactionSuggestion, setCompactionSuggestion] = useState(null);
  const [compactionStatus, setCompactionStatus] = useState("");
  const [compactionError, setCompactionError] = useState("");
  const [compactionStartedAt, setCompactionStartedAt] = useState(0);
  const [compactionPhaseLabel, setCompactionPhaseLabel] = useState("");
  const [compactionElapsedMs, setCompactionElapsedMs] = useState(null);
  const [compactionNowMs, setCompactionNowMs] = useState(() => Date.now());
  const initialScrollRef = useRef(false);
  const toolContinueLocksRef = useRef(new Set());
  const inputOffsetRef = useRef(null);
  const inputOffsetRafRef = useRef(null);
  const inputOffsetTimerRef = useRef(null);
  const composerRailRafRef = useRef(null);
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
  useEffect(() => {
    setCompactionPreview(null);
    setCompactionSuggestion(null);
    setCompactionStatus("");
    setCompactionError("");
    setCompactionStartedAt(0);
    setCompactionPhaseLabel("");
    setCompactionElapsedMs(null);
  }, [state.sessionId]);
  useEffect(() => {
    if (!compactionBusy || !compactionStartedAt) return undefined;
    const timer = window.setInterval(() => {
      setCompactionNowMs(Date.now());
    }, 250);
    return () => window.clearInterval(timer);
  }, [compactionBusy, compactionStartedAt]);
  const inlineToolsEnabled = toolDisplayMode !== "console";
  const shouldShowInlineToolsForMessage = useCallback(
    (msg, idx) => {
      if (!inlineToolsEnabled) return false;
      if (toolDisplayMode === "inline" || toolDisplayMode === "both") return true;
      if (!msg?.id) return false;
      if (activeMessageId && msg.id === activeMessageId) return true;
      if (highlightChainId && msg.id === highlightChainId) return true;
      const metadata =
        msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
      if (
        toolDisplayMode === "auto" &&
        msg.role === "ai" &&
        Array.isArray(msg.tools) &&
        msg.tools.length > 0 &&
        (metadata.tool_continued ||
          metadata.tool_response_pending ||
          normalizeToolContinuationPhases(metadata).length > 0)
      ) {
        return true;
      }
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

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handleToolReviewAction = (event) => {
      const detail = event?.detail || {};
      if (detail.handled) return;
      const action = normalizeToolReviewAction(detail.action);
      if (!action) return;
      const target = normalizeToolReviewTarget(detail);
      if (!clickInlineToolReviewButton(target, action)) return;
      detail.handled = true;
    };
    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewAction);
    return () => {
      window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewAction);
    };
  }, [clickInlineToolReviewButton]);

  const activeModeModel = useMemo(
    () => resolveModeModel(state.backendMode, state),
    [state.backendMode, state.apiModel, state.localModel, state.transformerModel],
  );
  const activeModelCapabilities = useMemo(() => {
    const mode = String(activeModeModel.mode || "").toLowerCase();
    const entries =
      mode === "server"
        ? state.serverModelDetails
        : mode === "api"
          ? state.apiModelCatalog
          : [];
    return resolveModelCapabilities(entries, activeModeModel.model);
  }, [
    activeModeModel.mode,
    activeModeModel.model,
    state.apiModelCatalog,
    state.serverModelDetails,
  ]);
  const outputLimitWarning = useMemo(() => {
    if (!outputTokenLimit || !activeModelCapabilities) return "";
    if (
      activeModelCapabilities.maxOutputTokens &&
      outputTokenLimit > activeModelCapabilities.maxOutputTokens
    ) {
      return `Selected response limit exceeds the reported ${formatTokenLimit(
        activeModelCapabilities.maxOutputTokens,
      )} maximum response.`;
    }
    if (
      activeModelCapabilities.maxContextLength &&
      outputTokenLimit >= activeModelCapabilities.maxContextLength
    ) {
      return `Selected response limit leaves no room for the prompt inside the reported ${formatTokenLimit(
        activeModelCapabilities.maxContextLength,
      )} context.`;
    }
    return "";
  }, [activeModelCapabilities, outputTokenLimit]);
  const outputLimitTooltipText = useMemo(() => {
    const source = activeModelCapabilities?.source
      ? ` Capacity source: ${activeModelCapabilities.source}.`
      : "";
    return `Context is the full working window for the prompt, history, retrieved memory, tools, and reply. The response limit caps only newly generated reply tokens; Auto leaves it to the provider. Reported capacity: ${formatTokenLimit(
      activeModelCapabilities?.maxContextLength,
    )} context and ${formatTokenLimit(
      activeModelCapabilities?.maxOutputTokens,
    )} maximum response.${source} Local runtimes may use a configured context below the model's theoretical maximum.`;
  }, [activeModelCapabilities]);
  const activeModelLabel = useMemo(
    () => {
      const displayModel =
        String(activeModeModel.mode || "").toLowerCase() === "api"
          ? formatApiModelLabel(activeModeModel.model, {
              aliases: state.apiModelAliases,
              availableModels: state.apiModels,
              catalog: state.apiModelCatalog,
            }) || activeModeModel.model
          : activeModeModel.model;
      return formatModelSourceLabel(activeModeModel.mode, displayModel);
    },
    [
      activeModeModel.mode,
      activeModeModel.model,
      state.apiModelAliases,
      state.apiModels,
    ],
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

  const applySubchatControlFromTools = useCallback(
    (tools) => {
      const control = resolveSubchatControlFromTools(tools);
      if (!control || control.action !== "return_to_parent") return;
      const parentId =
        control.parentSessionId ||
        String(parentConversationLink?.conversationId || "").trim();
      if (!parentId || typeof onOpenConversation !== "function") return;
      onOpenConversation(parentId, parentConversationLink?.label || "");
    },
    [onOpenConversation, parentConversationLink],
  );

  const getMessageSourceLabel = useCallback(
    (msg) => {
      if (!msg || typeof msg !== "object") return "";
      const meta = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
      const liveLabel = resolveLiveStreamSourceLabel(meta);
      if (liveLabel) return liveLabel;
      const mode = typeof meta.mode === "string" ? meta.mode : "";
      const modelCandidates = [
        meta.model_received,
        meta.model_resolved,
        meta.effective_model_id,
        meta.effective_model,
        meta.model,
      ];
      const resolvedModel =
        modelCandidates.find(
          (value) => typeof value === "string" && value.trim(),
        ) || "";
      const model =
        typeof resolvedModel === "string" ? resolvedModel.trim() : "";
      const provider =
        typeof meta.provider === "string" ? meta.provider.trim() : "";
      return formatRuntimeSourceLabel(mode, model || provider, provider);
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
          .catch((err) => void err);
      }
    } catch (err) {
      void err;
    }
  }, []);

  const syncHistoryFromConversation = useCallback(
    (sessionId, conversation) => {
      const history = buildHistoryFromConversation(conversation);
      persistHistorySnapshot(sessionId, history);
      return history;
    },
    [buildHistoryFromConversation, persistHistorySnapshot],
  );

  const buildLiveStreamMessageMetadata = useCallback(({ partial } = {}) => {
    const liveState = liveStreamStateRef.current;
    const runtime =
      liveState?.runtime && typeof liveState.runtime === "object"
        ? liveState.runtime
        : {};
    const liveMetadata = {
      source:
        (typeof runtime.source === "string" && runtime.source.trim()) || "live",
    };
    if (liveState?.sessionId) {
      liveMetadata.session_id = liveState.sessionId;
    }
    [
      "transport",
      "provider",
      "mode",
      "model",
      "voice",
      "multimodal_model",
    ].forEach((key) => {
      const value = runtime?.[key];
      if (typeof value === "string" && value.trim()) {
        liveMetadata[key] = value.trim();
      }
    });
    if (typeof partial === "boolean") {
      liveMetadata.partial = partial;
    }
    return { live_stream: liveMetadata };
  }, []);

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
              ...buildLiveStreamMessageMetadata({ partial }),
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
    [buildLiveStreamMessageMetadata, setState, syncHistoryFromConversation],
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
          metadata: buildLiveStreamMessageMetadata(),
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
    [buildLiveStreamMessageMetadata, setState, syncHistoryFromConversation],
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
            ...buildLiveStreamMessageMetadata({ partial: status !== "completed" }),
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
    [buildLiveStreamMessageMetadata, setState, syncHistoryFromConversation],
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
    } catch (err) {
      void err;
    }
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
      audioContext.close().catch((err) => void err);
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
      width: { ideal: 1920 },
      height: { ideal: 1080 },
      aspectRatio: { ideal: 16 / 9 },
      resizeMode: "none",
    };
    if (preferredCameraDeviceId) {
      video.deviceId = { exact: preferredCameraDeviceId };
    } else {
      video.facingMode = "environment";
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
      } catch (err) {
        void err;
      }
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
    setChatSettingsPopoverStyle(buildComposerOverlayStyle({
      anchorRect: trigger.getBoundingClientRect(),
      popoverRect: chatSettingsPopoverRef.current?.getBoundingClientRect(),
      zIndex: 3600,
    }));
  }, []);

  const updateAttachmentPopoverPosition = useCallback(() => {
    if (typeof window === "undefined") return;
    const trigger = attachmentTriggerRef.current;
    if (!trigger) return;
    setAttachmentPopoverStyle(buildComposerOverlayStyle({
      anchorRect: trigger.getBoundingClientRect(),
      popoverRect: attachmentPopoverRef.current?.getBoundingClientRect(),
      maxWidth: 240,
      gap: 8,
      zIndex: 3610,
    }));
  }, []);

  const updateInputAlertPosition = useCallback(() => {
    if (typeof window === "undefined") return;
    const composer = inputBoxRef.current;
    if (!composer) return;
    setInputAlertStyle(buildComposerOverlayStyle({
      anchorRect: composer.getBoundingClientRect(),
      popoverRect: inputAlertStackRef.current?.getBoundingClientRect(),
      maxWidth: 980,
      gap: 10,
      zIndex: 3620,
    }));
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

  const flushQueuedRealtimeAssistantResponse = useCallback(() => {
    const lifecycle = realtimeResponseLifecycleRef.current;
    if (!lifecycle.queued || lifecycle.active || lifecycle.requested) {
      return false;
    }
    const sent = sendRealtimeClientEvent({ type: "response.create" });
    if (sent) {
      lifecycle.requested = true;
      lifecycle.queued = false;
    }
    return sent;
  }, [sendRealtimeClientEvent]);

  const configureRealtimeTools = useCallback(async () => {
    if (realtimeConfiguredToolsRef.current) {
      flushQueuedRealtimeAssistantResponse();
      return true;
    }
    const channel = voiceChannelRef.current;
    if (!channel || channel.readyState !== "open") return false;
    if (realtimeToolSetupPromiseRef.current) {
      return realtimeToolSetupPromiseRef.current;
    }
    let setupPromise;
    setupPromise = (async () => {
      try {
        const res = await axios.get("/api/tools/specs", {
          params: { workflow: "live" },
        });
        const tools = Array.isArray(res?.data?.tools) ? res.data.tools : [];
        const usedRealtimeToolNames = new Set();
        const nextRealtimeToolNameMap = {};
        const liveTools = tools
          .filter((tool) => tool?.name && tool?.policy?.live_auto !== false)
          .map((tool) => {
            const canonicalName = String(tool.name || "").trim();
            const apiName = realtimeToolApiName(canonicalName, usedRealtimeToolNames);
            nextRealtimeToolNameMap[apiName] = canonicalName;
            nextRealtimeToolNameMap[canonicalName] = canonicalName;
            const descriptionParts = [];
            if (tool.description) descriptionParts.push(String(tool.description));
            if (apiName !== canonicalName) {
              descriptionParts.push(`Float tool name: ${canonicalName}.`);
            }
            return {
              type: "function",
              name: apiName,
              description: descriptionParts.join("\n"),
              parameters:
                tool.parameters && typeof tool.parameters === "object"
                  ? normalizeRealtimeToolSchema(tool.parameters)
                  : { type: "object", properties: {} },
            };
          });
        realtimeToolNameMapRef.current = nextRealtimeToolNameMap;
        const turnDetection = realtimeTurnDetectionRef.current || {};
        const transcriptionModel =
          typeof realtimeTranscriptionModelRef.current === "string" &&
          realtimeTranscriptionModelRef.current.trim()
            ? realtimeTranscriptionModelRef.current.trim()
            : "gpt-realtime-whisper";
        const sessionUpdate = {
          type: "realtime",
          audio: {
            input: {
              turn_detection: {
                type: turnDetection.type || "server_vad",
                create_response: false,
                interrupt_response: turnDetection.interrupt_response !== false,
              },
              transcription: {
                model: transcriptionModel,
              },
            },
          },
        };
        if (liveTools.length) {
          sessionUpdate.tool_choice = "auto";
          sessionUpdate.tools = liveTools;
        }
        const sent = sendRealtimeClientEvent({
          type: "session.update",
          session: sessionUpdate,
        });
        if (sent) {
          realtimeConfiguredToolsRef.current = true;
        }
        return sent;
      } catch (err) {
        console.error("realtime tool setup failed", err);
        return false;
      } finally {
        if (realtimeToolSetupPromiseRef.current === setupPromise) {
          realtimeToolSetupPromiseRef.current = null;
        }
        flushQueuedRealtimeAssistantResponse();
      }
    })();
    realtimeToolSetupPromiseRef.current = setupPromise;
    return setupPromise;
  }, [flushQueuedRealtimeAssistantResponse, sendRealtimeClientEvent]);

  const requestRealtimeAssistantResponse = useCallback(
    ({ force = false } = {}) => {
      const lifecycle = realtimeResponseLifecycleRef.current;
      if (!force && (lifecycle.active || lifecycle.requested || lifecycle.queued)) {
        return false;
      }
      if (!realtimeConfiguredToolsRef.current) {
        lifecycle.queued = true;
        configureRealtimeTools().catch(() => {});
        return false;
      }
      const sent = sendRealtimeClientEvent({ type: "response.create" });
      if (sent) {
        lifecycle.requested = true;
        lifecycle.queued = false;
      }
      return sent;
    },
    [configureRealtimeTools, sendRealtimeClientEvent],
  );

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
          workflow: "live",
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
      const canonicalName =
        realtimeToolNameMapRef.current[normalizedName] || normalizedName;
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
        name: canonicalName,
        realtimeName: normalizedName,
        argumentsText: typeof argumentsText === "string" ? argumentsText : "",
        handled: true,
      };
      realtimePendingToolCallsRef.current.add(normalizedCallId);
      realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
      await invokeRealtimeToolCall({
        callId: normalizedCallId,
        name: canonicalName,
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
    ttsRequestIdRef.current += 1;
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
      route: null,
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
    liveStreamStateRef.current = { sessionId: null, currentTurn: null, runtime: null };
    realtimeToolStateRef.current = {};
    realtimeToolNameMapRef.current = {};
    realtimePendingToolCallsRef.current = new Set();
    realtimeToolSetupPromiseRef.current = null;
    realtimeConfiguredToolsRef.current = false;
    realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
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
      realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
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
        ...createRealtimeResponseLifecycleState(),
        active: true,
      };
      setLiveStreamingPhase("assistant-thinking");
      return;
    }
    if (type === "conversation.item.created" || type === "conversation.item.added") {
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
    if (type === "response.output_item.done" || type === "conversation.item.done") {
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
      realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
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
      realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
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
      const sessionTranscriptionModel =
        session?.session?.audio?.input?.transcription?.model ||
        session?.runtime?.realtime_transcription_model ||
        "gpt-realtime-whisper";
      realtimeTurnDetectionRef.current = {
        type:
          typeof sessionTurnDetection?.type === "string" &&
          sessionTurnDetection.type.trim()
            ? sessionTurnDetection.type.trim()
            : "server_vad",
        interrupt_response: sessionTurnDetection?.interrupt_response !== false,
      };
      realtimeTranscriptionModelRef.current = String(
        sessionTranscriptionModel || "gpt-realtime-whisper",
      ).trim();
      realtimeToolNameMapRef.current = {};
      realtimePendingToolCallsRef.current = new Set();
      realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();

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
        realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
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
        runtime: resolveLiveSessionRuntime(
          session,
          "api",
          getSelectedTargetModelForMode(state, "api"),
        ),
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
      state,
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
    realtimeResponseLifecycleRef.current = createRealtimeResponseLifecycleState();
    liveStreamStateRef.current = {
      sessionId: createClientMessageId("livekit-session"),
      currentTurn: null,
      runtime: resolveLiveSessionRuntime(
        session,
        state.backendMode,
        getSelectedTargetModelForMode(state, state.backendMode),
      ),
    };
    setLiveStreamingTranscript({ user: "", assistant: "" });
    setLiveStreamingPhase("listening");
    setLiveSessionPending(false);
    setRecording(true);
  }, [createProcessedAudioInput, ensureLiveSessionAttemptCurrent, state]);

  const toggleCollapseAllTools = useCallback(() => {
    setCollapseAllTools((prev) => !prev);
    setCollapsedTools({});
    setExpandedToolCards({});
  }, []);

  const toggleToolCollapse = useCallback(
    (messageId) => {
      if (!messageId) return;
      setExpandedToolCards({});
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
      if (
        messageId &&
        ttsActiveMessageId === messageId &&
        ttsPlayback.status === "loading"
      ) {
        return;
      }
      stopTtsPlayback();
      const requestId = ttsRequestIdRef.current + 1;
      ttsRequestIdRef.current = requestId;
      setTtsActiveMessageId(messageId);
      const requestedRoute = resolveTtsRoute(state.ttsModel, state.voiceModel);
      setTtsPlayback({
        messageId,
        status: "loading",
        currentTime: 0,
        duration: 0,
        route: requestedRoute,
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
        if (ttsRequestIdRef.current !== requestId) {
          return;
        }
        const audioB64 = res?.data?.audio_b64;
        const contentType = res?.data?.content_type || "audio/wav";
        if (!audioB64) {
          throw new Error("No audio returned from TTS");
        }
        const resolvedRoute = resolveTtsRoute(
          payload.model,
          payload.voice,
          res?.data || {},
        );
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
                  route: resolvedRoute,
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
                  route: resolvedRoute,
                }
              : prev,
          );
        };
        audio.onplay = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId
              ? { ...prev, status: "playing", route: resolvedRoute }
              : prev,
          );
        };
        audio.onpause = () => {
          setTtsPlayback((prev) =>
            prev.messageId === messageId
              ? { ...prev, status: "paused", route: resolvedRoute }
              : prev,
          );
        };
        audio.onended = () => {
          stopTtsPlayback();
        };
        audio.onerror = () => {
          stopTtsPlayback();
        };
        await audio.play();
        if (ttsRequestIdRef.current !== requestId) {
          try {
            audio.pause();
            audio.src = "";
          } catch (_) {}
          if (ttsAudioRef.current === audio) {
            ttsAudioRef.current = null;
          }
          return;
        }
        setTtsPlayback((prev) =>
          prev.messageId === messageId
            ? { ...prev, status: "playing", route: resolvedRoute }
            : prev,
        );
      } catch (err) {
        if (ttsRequestIdRef.current !== requestId) {
          return;
        }
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
    [
      outputVolume,
      state.ttsModel,
      state.voiceModel,
      stopTtsPlayback,
      ttsActiveMessageId,
      ttsPlayback.status,
    ],
  );
  const clearActiveRequest = useCallback(() => {
    activeRequestActivityRef.current?.clear?.();
    activeRequestActivityRef.current = null;
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
  useEffect(() => {
    const activeRequest = activeRequestActivityRef.current;
    if (
      activeRequest &&
      streamActivityMatchesRequest(streamActivity, activeRequest.messageId)
    ) {
      activeRequest.markActivity();
    }
  }, [streamActivity]);

  useEffect(
    () => () => {
      activeRequestActivityRef.current?.clear?.();
      activeRequestActivityRef.current = null;
    },
    [],
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
  const applyChatWindowWidth = useCallback((width, { persist = true } = {}) => {
    const root = typeof document !== "undefined" ? document.documentElement : null;
    if (!root) return;
    root.style.setProperty("--center-rail-width", `${width}px`);
    if (!persist) return;
    try {
      localStorage.setItem(CHAT_WINDOW_STORAGE_KEY, String(width));
    } catch {}
  }, []);
  const resetChatWindowWidth = useCallback(() => {
    const root = typeof document !== "undefined" ? document.documentElement : null;
    if (!root) return;
    root.style.removeProperty("--center-rail-width");
    try {
      localStorage.removeItem(CHAT_WINDOW_STORAGE_KEY);
    } catch {}
  }, []);
  const getCurrentChatWindowWidth = useCallback(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return getChatWindowWidthBounds(1440).minWidth;
    }
    const rootStyles = window.getComputedStyle(document.documentElement);
    const configuredWidth = Number.parseFloat(
      rootStyles.getPropertyValue("--center-rail-width") || "",
    );
    if (Number.isFinite(configuredWidth) && configuredWidth > 0) {
      return configuredWidth;
    }
    const fallbackWidth = chatContainerRef.current?.getBoundingClientRect().width;
    if (Number.isFinite(fallbackWidth) && fallbackWidth > 0) {
      return Math.round(fallbackWidth + 24);
    }
    return getChatWindowWidthBounds(window.innerWidth).minWidth;
  }, []);
  const nudgeChatWindowWidth = useCallback(
    (delta) => {
      if (typeof window === "undefined" || !canResizeChatWindow(window.innerWidth)) return;
      const currentWidth = getCurrentChatWindowWidth();
      const next = clampChatWindowWidth(currentWidth + delta, window.innerWidth);
      applyChatWindowWidth(next);
    },
    [applyChatWindowWidth, getCurrentChatWindowWidth],
  );
  const handleChatWindowResizeKeyDown = useCallback(
    (event) => {
      if (typeof window === "undefined" || !canResizeChatWindow(window.innerWidth)) return;
      if (event.key === "Home") {
        event.preventDefault();
        resetChatWindowWidth();
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey
        ? CHAT_WINDOW_KEYBOARD_STEP_FAST
        : CHAT_WINDOW_KEYBOARD_STEP;
      const delta = event.key === "ArrowRight" ? step : -step;
      nudgeChatWindowWidth(delta);
    },
    [nudgeChatWindowWidth, resetChatWindowWidth],
  );
  const startChatWindowResize = useCallback(
    (event) => {
      if (typeof window === "undefined" || !canResizeChatWindow(window.innerWidth)) return;
      if (event.button !== 0 && event.pointerType !== "touch") return;
      event.preventDefault();
      const root = document.documentElement;
      const startX = event.clientX;
      const startWidth = getCurrentChatWindowWidth();
      const { minWidth, maxWidth } = getChatWindowWidthBounds(window.innerWidth);
      root.style.setProperty("cursor", "col-resize");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.body.classList.add("is-layout-resizing");
      setIsChatWindowResizing(true);
      let lastWidth = startWidth;
      let frameId = null;

      const flushWidth = (persist = false) => {
        if (frameId !== null && typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(frameId);
          frameId = null;
        }
        applyChatWindowWidth(lastWidth, { persist });
      };

      const onMove = (moveEvent) => {
        const delta = (moveEvent.clientX - startX) * 2;
        lastWidth = Math.min(maxWidth, Math.max(minWidth, Math.round(startWidth + delta)));
        if (typeof requestAnimationFrame !== "function") {
          applyChatWindowWidth(lastWidth, { persist: false });
          return;
        }
        if (frameId !== null) return;
        frameId = requestAnimationFrame(() => {
          frameId = null;
          applyChatWindowWidth(lastWidth, { persist: false });
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
        setIsChatWindowResizing(false);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [applyChatWindowWidth, getCurrentChatWindowWidth],
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
      const {
        trackAbort = true,
        endpoint = "/api/llm/generate",
        allowRetry = true,
      } = options;
      const attemptRequest = async (attemptIndex) => {
        const timeoutMs = computeAdaptiveTimeoutMs(text, attemptIndex);
        const canAbort = typeof AbortController !== "undefined";
        const controller = canAbort ? new AbortController() : null;
        const inactivityTimer = controller
          ? createRequestInactivityTimer({
              timeoutMs,
              onTimeout: () => {
                if (!controller.signal?.aborted) {
                  controller.abort("timeout");
                }
              },
            })
          : null;
        if (controller && trackAbort) {
          activeRequestRef.current = controller;
          activeRequestActivityRef.current = {
            controller,
            messageId: payload?.message_id,
            markActivity: inactivityTimer.markActivity,
            clear: inactivityTimer.clear,
          };
        }
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
              `Stopped waiting after ${Math.round(timeoutMs / 1000)}s without response activity. Try again or adjust model settings.`,
            );
            timeoutError.code = "REQUEST_TIMEOUT";
            timeoutError.timeoutMs = timeoutMs;
            timeoutError.cause = error;
            throw timeoutError;
          }
          throw error;
        } finally {
          inactivityTimer?.clear();
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
        if (!allowRetry || !shouldRetry(err)) {
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
    (messageId, behavior = "smooth", options = {}) => {
      if (!messageId || !messageRefs.current[messageId]) return;
      const node = resolveScrollContainer();
      const target = messageRefs.current[messageId];
      if (!node || !target || typeof target.getBoundingClientRect !== "function") {
        target?.scrollIntoView?.({ behavior, block: options?.block || "start" });
        return;
      }
      const containerRect = node.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const absoluteTop = targetRect.top - containerRect.top + node.scrollTop;
      const absoluteBottom =
        targetRect.bottom - containerRect.top + node.scrollTop;
      const topInset = Math.min(96, Math.max(48, node.clientHeight * 0.08));
      const bottomInset = Math.min(96, Math.max(32, node.clientHeight * 0.18));
      const block = options?.block || "start";
      const nextTop =
        block === "end"
          ? Math.max(0, absoluteBottom - (node.clientHeight - bottomInset))
          : Math.max(0, absoluteTop - topInset);
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
    if (typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const root = document.documentElement;
    const scheduleSync = () => {
      if (composerRailRafRef.current !== null) return;
      composerRailRafRef.current = window.requestAnimationFrame(() => {
        composerRailRafRef.current = null;
        const node = chatContainerRef.current;
        if (!node || typeof node.getBoundingClientRect !== "function") {
          root.style.removeProperty("--chat-composer-center");
          root.style.removeProperty("--chat-composer-width");
          return;
        }
        const rect = node.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) return;
        root.style.setProperty(
          "--chat-composer-center",
          `${Math.round(rect.left + rect.width / 2)}px`,
        );
        root.style.setProperty(
          "--chat-composer-width",
          `${Math.max(320, Math.round(rect.width - 24))}px`,
        );
      });
    };

    scheduleSync();
    window.addEventListener("resize", scheduleSync);
    let observer = null;
    if (typeof ResizeObserver !== "undefined" && chatContainerRef.current) {
      observer = new ResizeObserver(scheduleSync);
      observer.observe(chatContainerRef.current);
    }
    return () => {
      window.removeEventListener("resize", scheduleSync);
      observer?.disconnect();
      if (composerRailRafRef.current !== null) {
        window.cancelAnimationFrame(composerRailRafRef.current);
        composerRailRafRef.current = null;
      }
      root.style.removeProperty("--chat-composer-center");
      root.style.removeProperty("--chat-composer-width");
    };
  }, []);

  useEffect(() => {
    if (!state || !state.sessionId) return;
    initialScrollRef.current = false;
    messageRefs.current = {};
  }, [state.sessionId]);

  useEffect(() => {
    setCollapseAllTools(true);
    setCollapsedTools({});
    setExpandedToolCards({});
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
      scrollMessageIntoView(activeMessageId, "smooth", { block: "start" });
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
    if (!/^blob:/i.test(String(attachment.url))) return;
    try {
      URL.revokeObjectURL(attachment.url);
    } catch (_) {}
  }, []);

  const clearComposerAttachments = useCallback((options = {}) => {
    const submissionSessionId =
      options.sessionId || activeComposerSessionRef.current;
    const submittedAttachments = Array.isArray(options.attachments)
      ? options.attachments
      : attachmentsRef.current;
    const attachmentIds = submittedAttachments
      .map(
        (attachment) =>
          attachment?.outboxId || attachment?.outbox_id || attachment?.id,
      )
      .filter(Boolean);
    if (!attachmentIds.length) return;

    const submittedIds = new Set(attachmentIds);
    submittedAttachments.forEach((attachment) => {
      const attachmentId =
        attachment?.outboxId || attachment?.outbox_id || attachment?.id;
      if (attachmentId) removedAttachmentIdsRef.current.add(attachmentId);
    });
    removeStoredComposerDraftAttachments(
      submissionSessionId,
      submittedAttachments,
    );
    markStoredComposerAttachmentTombstones(
      submissionSessionId,
      attachmentIds,
    );
    void deleteSentAttachmentOutboxEntries(
      submissionSessionId,
      attachmentIds,
    );

    if (activeComposerSessionRef.current !== submissionSessionId) return;
    setAttachments((currentAttachments) => {
      const next = currentAttachments.filter((attachment) => {
        const attachmentId =
          attachment?.outboxId || attachment?.outbox_id || attachment?.id;
        if (!attachmentId || !submittedIds.has(attachmentId)) return true;
        revokeAttachmentPreview(attachment);
        return false;
      });
      attachmentsRef.current = next;
      return next;
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [revokeAttachmentPreview]);

  useEffect(() => {
    void cleanupExpiredAttachmentOutboxEntries();
  }, []);

  useEffect(() => {
    if (composerDraftSessionRef.current !== state.sessionId) {
      const nextDraft = readStoredComposerDraft(state.sessionId);
      composerDraftSessionRef.current = state.sessionId;
      setMessage(nextDraft.message || "");
      setComposerCursor((nextDraft.message || "").length);
      setAttachments((previous) => {
        previous.forEach(revokeAttachmentPreview);
        return Array.isArray(nextDraft.attachments) ? nextDraft.attachments : [];
      });
      setVisionWorkflow(normalizeVisionWorkflow(nextDraft.visionWorkflow));
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return;
    }
    writeStoredComposerDraft(state.sessionId, {
      message,
      attachments,
      visionWorkflow,
    });
  }, [attachments, message, revokeAttachmentPreview, state.sessionId, visionWorkflow]);

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
    if (!cameraOpen) return undefined;
    const handleEscape = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      stopCameraCapture();
      window.requestAnimationFrame(() => attachmentTriggerRef.current?.focus());
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [cameraOpen, stopCameraCapture]);

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
    if (!chatSettingsOpen || chatSettingsSection !== "workflow") return undefined;
    let cancelled = false;
    setWorkflowCatalogResolved(false);
    axios
      .get("/api/workflows/catalog")
      .then((response) => {
        if (!cancelled) {
          setChatWorkflowProfiles(normalizeWorkflowProfiles(response?.data));
          setWorkflowCatalogResolved(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChatWorkflowProfiles(FALLBACK_WORKFLOW_PROFILES);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chatSettingsOpen, chatSettingsSection]);

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
        window.requestAnimationFrame(() => chatSettingsTriggerRef.current?.focus());
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
      if (attachmentPopoverRef.current?.contains(target)) return;
      setAttachmentMenuOpen(false);
    };
    const handleEscape = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setAttachmentMenuOpen(false);
        window.requestAnimationFrame(() => attachmentTriggerRef.current?.focus());
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
    if (!attachmentMenuOpen) {
      setAttachmentPopoverStyle(null);
      return undefined;
    }
    let frameId = null;
    const syncPosition = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        updateAttachmentPopoverPosition();
      });
    };
    syncPosition();
    const resizeObserver =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(syncPosition)
        : null;
    if (attachmentPopoverRef.current) {
      resizeObserver?.observe(attachmentPopoverRef.current);
    }
    if (attachmentTriggerRef.current) {
      resizeObserver?.observe(attachmentTriggerRef.current);
    }
    const visualViewport = window.visualViewport;
    window.addEventListener("resize", syncPosition);
    window.addEventListener("scroll", syncPosition, true);
    visualViewport?.addEventListener("resize", syncPosition);
    visualViewport?.addEventListener("scroll", syncPosition);
    const focusFrameId = window.requestAnimationFrame(() => {
      attachmentPopoverRef.current
        ?.querySelector("button")
        ?.focus();
    });
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.cancelAnimationFrame(focusFrameId);
      window.removeEventListener("resize", syncPosition);
      window.removeEventListener("scroll", syncPosition, true);
      visualViewport?.removeEventListener("resize", syncPosition);
      visualViewport?.removeEventListener("scroll", syncPosition);
      resizeObserver?.disconnect();
    };
  }, [attachmentMenuOpen, updateAttachmentPopoverPosition]);

  useEffect(() => {
    if (!inputAlerts.length) {
      setInputAlertStyle(null);
      return undefined;
    }
    let frameId = null;
    const syncPosition = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        updateInputAlertPosition();
      });
    };
    syncPosition();
    const resizeObserver =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(syncPosition)
        : null;
    if (inputAlertStackRef.current) {
      resizeObserver?.observe(inputAlertStackRef.current);
    }
    if (inputBoxRef.current) {
      resizeObserver?.observe(inputBoxRef.current);
    }
    const visualViewport = window.visualViewport;
    window.addEventListener("resize", syncPosition);
    window.addEventListener("scroll", syncPosition, true);
    visualViewport?.addEventListener("resize", syncPosition);
    visualViewport?.addEventListener("scroll", syncPosition);
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.removeEventListener("resize", syncPosition);
      window.removeEventListener("scroll", syncPosition, true);
      visualViewport?.removeEventListener("resize", syncPosition);
      visualViewport?.removeEventListener("scroll", syncPosition);
      resizeObserver?.disconnect();
    };
  }, [inputAlerts.length, inputAlertsKey, updateInputAlertPosition]);

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
    const resizeObserver =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(syncPosition)
        : null;
    if (chatSettingsPopoverRef.current) {
      resizeObserver?.observe(chatSettingsPopoverRef.current);
    }
    if (chatSettingsTriggerRef.current) {
      resizeObserver?.observe(chatSettingsTriggerRef.current);
    }
    const visualViewport = window.visualViewport;
    window.addEventListener("resize", syncPosition);
    window.addEventListener("scroll", syncPosition, true);
    visualViewport?.addEventListener("resize", syncPosition);
    visualViewport?.addEventListener("scroll", syncPosition);
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.removeEventListener("resize", syncPosition);
      window.removeEventListener("scroll", syncPosition, true);
      visualViewport?.removeEventListener("resize", syncPosition);
      visualViewport?.removeEventListener("scroll", syncPosition);
      resizeObserver?.disconnect();
    };
  }, [chatSettingsOpen, chatSettingsSection, updateChatSettingsPopoverPosition]);

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
      setCameraError(getRequestErrorDetail(err, "Camera access failed."));
    } finally {
      setCameraBusy(false);
    }
  }, [buildCameraConstraints, cameraOpen, refreshAvailableInputDevices, stopCameraCapture]);

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
    const rawDisplayMessage = typeof msg === "string" ? msg : "";
    const composerSubmission = prepareComposerSubmission(
      rawDisplayMessage,
      attachments.length,
    );
    const effectiveVisionWorkflow = hasImageAttachments ? visionWorkflow : "auto";
    if (effectiveVisionWorkflow === "compare" && imageAttachmentCount < 2) {
      setError("Compare mode needs at least two image attachments.");
      return;
    }
    const { displayMessage, shouldSend } = composerSubmission;
    if (!shouldSend) return;
    const commandRequest = buildCommandAwareRequest(displayMessage);
    const effectiveMessage = commandRequest.requestMessage;
    if (attachments.some((a) => a.uploading)) {
      setError("Attachments are still uploading. Please wait.");
      return;
    }
    if (attachments.some((attachment) => !attachment?.remoteUrl)) {
      setError("Retry or remove failed attachments before sending.");
      return;
    }
    const submissionSessionId = state.sessionId;
    const submittedAttachments = attachments.map((attachment) => ({
      ...attachment,
    }));
    abortActiveRequest();
    clearActiveRequest();
    const normalizedAttachments = submittedAttachments.map((a) => {
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
        source_url: a.source_url || a.sourceUrl || null,
        source_url_recorded_at:
          a.source_url_recorded_at || a.sourceUrlRecordedAt || null,
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
           source_url,
           source_url_recorded_at,
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
           source_url,
           source_url_recorded_at,
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
           source_url,
           source_url_recorded_at,
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
           source_url,
           source_url_recorded_at,
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
    const msgId = createClientMessageId("msg");
    setActiveMessageId && setActiveMessageId(msgId);
    debugLog("Sending message:", displayMessage);

    try {
      memoryStore["last_message"] = { content: displayMessage, importance: 5 };
    setState((prev) => {
      const newHistory = [...prev.history, { role: "user", text: displayMessage }];
        const attachmentsForState = conversationAttachments.map((att) => ({ ...att }));
        const timestampIso = new Date().toISOString();
        const newState = {
          ...prev,
          conversation: [
            ...prev.conversation,
            {
              id: `${msgId}:user`,
              role: "user",
              text: displayMessage,
              timestamp: timestampIso,
              attachments: attachmentsForState,
              metadata: {
                client_outbox: true,
                ...(hasImageAttachments || effectiveVisionWorkflow !== "auto"
                  ? { vision: { workflow: effectiveVisionWorkflow } }
                  : {}),
              },
            },
            {
              role: "ai",
              id: msgId,
              text: "",
              thoughts: [],
              tools: [],
              timestamp: timestampIso,
              metadata: { status: "pending", client_outbox: true },
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
      setComposerCursor(0);
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
          const res = await apiWrapper.chat(
            {
              message: effectiveMessage,
              mode: "api",
              session_id: state.sessionId,
              model: state.apiModel,
              message_id: msgId,
              attachments: apiAttachments,
              vision_workflow: effectiveVisionWorkflow,
              ...workflowPayload,
              ...generationControlPayload,
              ...ragPayload,
            },
            { signal: controller?.signal },
          );
          debugLog("API Response:", res);
          if (res?.cancelled) {
            const userCancelError = new Error("Generation cancelled");
            userCancelError.code = "USER_CANCELLED";
            throw userCancelError;
          }
          if (res.error) {
            throw createApiWrapperError(res);
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
        const model = resolveRequestModelForMode({
          backendMode: mode,
          apiModel: state.apiModel,
          transformerModel: state.transformerModel,
          localModel: state.localModel,
        });
        const payload = {
          message: effectiveMessage,
          mode,
          session_id: state.sessionId,
          message_id: msgId,
          model,
          attachments: apiAttachments,
          vision_workflow: effectiveVisionWorkflow,
          ...workflowPayload,
          ...generationControlPayload,
          ...ragPayload,
        };
        const r = await requestModelCompletion(payload, effectiveMessage, {
          trackAbort: true,
          endpoint: "/api/chat",
          // Persisted chat requests already reserve a canonical message_id server-side.
          // Retrying the same turn here duplicates backend work and can scramble ordering.
          allowRetry: false,
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
      if (
        commandRequest.toolDirective &&
        !responseUsesToolName(responseTools, commandRequest.toolDirective.toolName)
      ) {
        const fallbackTool = buildCommandFallbackTool(commandRequest.toolDirective, msgId);
        if (fallbackTool) {
          responseTools = mergeToolEntries(responseTools, [fallbackTool], null, {
            includeInlineMetadata: false,
          });
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
        const updatedConversation = acknowledgeClientOutboxPair(
          prev.conversation,
          msgId,
        );
        const idx = updatedConversation.findIndex((m) => m && m.id === msgId);
        if (idx !== -1) {
          const entry = { ...updatedConversation[idx], text: aiResponse };
          if (responseMetadata && Object.keys(responseMetadata).length) {
            entry.metadata = mergeAssistantMessageMetadata(
              entry.metadata,
              responseMetadata,
            );
          } else if (aiResponse && aiResponse.trim()) {
            entry.metadata = mergeAssistantMessageMetadata(entry.metadata, {
              status: "complete",
            });
          }
          if (entry.metadata && typeof entry.metadata === "object") {
            entry.metadata = { ...entry.metadata };
            delete entry.metadata.client_outbox;
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
      applySubchatControlFromTools(responseTools);
      clearComposerAttachments({
        sessionId: submissionSessionId,
        attachments: submittedAttachments,
      });
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
          hint: "No matching reasoning, response, or tool activity arrived before the inactivity timeout. Try again or raise the limit in Settings.",
          category: "timeout",
          actions,
        });
      } else if (isContextOverflowDetail(detail)) {
        const actions = [
          { label: "Preview compaction", onClick: () => previewConversationCompaction() },
          { label: "Settings", onClick: () => navigate("/settings") },
        ];
        if (state.backendMode === "api") {
          actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
        }
        setBanner({
          message: detail,
          hint: "The conversation exceeded the current context budget. Review the compaction suggestion to keep working with a condensed copy.",
          category: "context_overflow",
          actions,
        });
        planConversationCompaction().catch(() => {});
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
    setError(null);
    setBanner(null);
    setLoading(true);
    setIsStreaming(true);
    setRegeneratingMessageId(msg.id);
    setActiveMessageId && setActiveMessageId(msg.id);
    announceToolContinuationAttemptReset({
      sessionId: state.sessionId,
      messageId: msg.id,
    });
    const regenerateTarget = resolveRegenerateRequestTarget(state);
    let responseMetadata = null;
    let ragMatchesFromResponse = [];
    let responseThought = "";
    let responseTools = [];
    try {
      let aiResponse = "";
      if (regenerateTarget.mode === "api") {
        const controller =
          typeof AbortController !== "undefined" ? new AbortController() : null;
        if (controller) {
          activeRequestRef.current = controller;
        }
        try {
          const res = await apiWrapper.chat(
            {
              message: userText,
              mode: "api",
              session_id: state.sessionId,
              model: regenerateTarget.model || state.apiModel,
              message_id: msg.id,
              regenerate: true,
              attachments: previousAttachments,
              vision_workflow: previousVisionWorkflow,
              ...workflowPayload,
              ...generationControlPayload,
              ...ragPayload,
            },
            { signal: controller?.signal },
          );
          if (res?.cancelled) {
            const userCancelError = new Error("Generation cancelled");
            userCancelError.code = "USER_CANCELLED";
            throw userCancelError;
          }
          if (res.error) {
            throw createApiWrapperError(res);
          }
          aiResponse = res.message;
          responseThought = res.thought || "";
          responseTools = Array.isArray(res?.tools_used) ? res.tools_used : [];
          const md = res.metadata || {};
          responseMetadata = Object.keys(md).length ? md : null;
          ragMatchesFromResponse = ragMatchesFromSection(md?.rag);
          const responseError = getRegenerationResponseError(md, aiResponse);
          if (responseError) throw responseError;
          if (md.warning) {
            setBanner({
              message: md.warning,
              hint: md.hint,
              category: md.category || "api_warning",
              actions: [
                { label: "Settings", onClick: () => navigate("/settings") },
                { label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) },
              ],
            });
          } else {
            setBanner(null);
          }
        } finally {
          if (controller && activeRequestRef.current === controller) {
            clearActiveRequest();
          }
        }
      } else if (
        regenerateTarget.mode === "local" ||
        regenerateTarget.mode === "server"
      ) {
        const mode = regenerateTarget.mode;
        const model =
          regenerateTarget.model || getSelectedTargetModelForMode(state, mode);
        const payload = {
          message: userText,
          mode,
          session_id: state.sessionId,
          message_id: msg.id,
          regenerate: true,
          model,
          attachments: previousAttachments,
          vision_workflow: previousVisionWorkflow,
          ...workflowPayload,
          ...generationControlPayload,
          ...ragPayload,
        };
        const r = await requestModelCompletion(payload, userText, {
          trackAbort: true,
          endpoint: "/api/chat",
          allowRetry: false,
        });
        aiResponse = r.data?.message || "";
        responseThought = r.data?.thought || "";
        responseTools = Array.isArray(r.data?.tools_used) ? r.data.tools_used : [];
        const meta = r.data?.metadata || {};
        responseMetadata = Object.keys(meta).length ? meta : null;
        ragMatchesFromResponse = ragMatchesFromSection(meta?.rag);
        const responseError = getRegenerationResponseError(meta, aiResponse);
        if (responseError) throw responseError;
        if (meta?.warning) {
          const actions = [{ label: "Settings", onClick: () => navigate("/settings") }];
          if (mode === "server") {
            actions.push({ label: "Use local", onClick: () => setState((prev) => ({ ...prev, backendMode: "local" })) });
          }
          setBanner({
            message: meta.warning,
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

      // Reasoning and retrieval context are diagnostics, not a replacement
      // answer. Keep the saved response unless regeneration produced text or
      // an actionable tool proposal the transcript can actually render.
      const hasUserVisibleRegenerationOutput =
        (typeof aiResponse === "string" && aiResponse.trim()) ||
        responseTools.length > 0;
      if (!hasUserVisibleRegenerationOutput) {
        const emptyResponseError = new Error(
          "Regeneration completed without returning a final answer.",
        );
        emptyResponseError.code = "EMPTY_REGENERATION_RESPONSE";
        throw emptyResponseError;
      }

      setState((prev) => {
        const updated = [...prev.conversation];
        const mIdx = updated.findIndex((m) => m.id === msg.id);
        if (mIdx !== -1) {
          if (overrideUserText != null) {
            let userIdx = updated.findIndex(
              (m) => m && m.id === `${msg.id}:user`,
            );
            if (userIdx === -1 && mIdx > 0 && updated[mIdx - 1]?.role === "user") {
              userIdx = mIdx - 1;
            }
            if (userIdx !== -1) {
              updated[userIdx] = {
                ...updated[userIdx],
                text: overrideUserText,
                timestamp: new Date().toISOString(),
              };
            }
          }
          const cleanMetadata = clearRegenerationContinuationMetadata(
            updated[mIdx]?.metadata,
          );
          [
            ...RESPONSE_FAILURE_METADATA_KEYS,
            "rag",
            "inline_tool_payload",
            "inline_tool_payloads",
          ].forEach((key) => {
            delete cleanMetadata[key];
          });
          const entry = {
            ...updated[mIdx],
            text: aiResponse,
            content: aiResponse,
            thoughts: [],
            tools: [],
            ragMatches: [],
            timestamp: new Date().toISOString(),
            metadata: mergeAssistantMessageMetadata(cleanMetadata, {
              status: "complete",
              tool_response_pending: false,
              tool_continued: false,
              tool_continuation_phases: [],
              tool_continuation_text: "",
              tool_prelude_text: "",
              ...(responseMetadata || {}),
            }),
          };
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
      const isEmptyRegenerationError =
        err && err.code === "EMPTY_REGENERATION_RESPONSE";
      const isProviderRegenerationError =
        err && err.code === "REGENERATION_PROVIDER_ERROR";
      const detail =
        isTimeoutError
          ? err.message
          : getRequestErrorDetail(err, "Request failed");
      setError(detail);
      if (isRegenerateMessageConflict(err, detail)) {
        setBanner({
          message: detail,
          hint: "Float could not safely replace the saved turn, so the previous answer was kept. Reload the conversation and retry regeneration.",
          category: "regenerate_conflict",
          actions: [],
        });
      } else if (isEmptyRegenerationError) {
        setBanner({
          message: detail,
          hint: "The model returned no user-visible answer, so the previous answer was kept. Retry, switch models, or reduce reasoning effort.",
          category: "empty_response",
          actions: [{ label: "Settings", onClick: () => navigate("/settings") }],
        });
      } else if (isProviderRegenerationError) {
        const failureMetadata =
          err?.metadata && typeof err.metadata === "object" ? err.metadata : {};
        const actions = [
          { label: "Settings", onClick: () => navigate("/settings") },
        ];
        if (
          regenerateTarget.mode === "api" ||
          regenerateTarget.mode === "server"
        ) {
          actions.push({
            label: "Use local",
            onClick: () =>
              setState((prev) => ({ ...prev, backendMode: "local" })),
          });
        }
        setBanner({
          message: detail,
          hint:
            failureMetadata.hint ||
            "The previous answer was kept. Verify the selected provider and retry regeneration.",
          category: failureMetadata.category || "regenerate_error",
          actions,
        });
      } else if (isTimeoutError) {
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
      setRegeneratingMessageId(null);
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
      if (!hasInvokedToolResults(msg)) return false;
      const status = typeof meta.status === "string" ? meta.status.trim().toLowerCase() : "";
      const text = typeof msg.text === "string" ? msg.text : String(msg.text || "");
      const hasRenderableContent = hasRenderableAssistantContent(text);
      const isPlaceholder = isContinuationPlaceholderText(text);
      if (meta.tool_continued && hasRenderableContent && !isPlaceholder) return false;
      if (meta.tool_response_pending) return true;
      if (isPlaceholder) return true;
      if (!hasRenderableContent) return true;
      return status === "pending" || status === "proposed" || status === "streaming";
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
      let continuationLockKey = "";
      let continuationLockAcquired = false;
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
        const toolSource = Array.isArray(options?.toolsOverride)
          ? options.toolsOverride
          : msg.tools;
        const toolPayload = Array.isArray(toolSource)
          ? toolSource
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
        continuationLockKey = buildToolContinuationLockKey({
          sessionId: state.sessionId,
          messageId: msg.id,
          tools: toolPayload,
        });
        continuationLockAcquired =
          !continuationLockKey || acquireToolContinuationLock(continuationLockKey);
        if (!continuationLockAcquired) return;
        const res = await axios.post("/api/chat/continue", {
          session_id: state.sessionId,
          message_id: msg.id,
          model: resolvedModel,
          mode: resolvedMode,
          workflow: overrideWorkflow || workflowProfile,
          // Provide fallback results so denials can still unblock continuation.
          tools: toolPayload,
          ...generationControlPayload,
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
            const nextMetadataBase = {
              ...(updated[mIdx]?.metadata || {}),
              ...(md || {}),
              ...(Object.prototype.hasOwnProperty.call(
                md && typeof md === "object" ? md : {},
                "tool_response_pending",
              )
                ? { tool_response_pending: md.tool_response_pending }
                : { tool_response_pending: false }),
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
            };
            const nextMetadata = appendToolContinuationPhase(
              nextMetadataBase,
              existingText,
              aiContinuation,
            );
            nextMetadata.inline_tool_continuation_pending = false;
            if (
              returnedTools.length === 0 &&
              !Object.prototype.hasOwnProperty.call(md || {}, "inline_tool_payload") &&
              !Object.prototype.hasOwnProperty.call(md || {}, "inline_tool_payloads")
            ) {
              delete nextMetadata.inline_tool_payload;
              delete nextMetadata.inline_tool_payloads;
            }
            const updatedEntry = {
              ...updated[mIdx],
              text: joined,
              timestamp: new Date().toISOString(),
              ...(mergedTools.length ? { tools: mergedTools } : {}),
              metadata: nextMetadata,
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
        applySubchatControlFromTools(returnedTools);
      } catch (err) {
        const detail =
          (err && err.response && err.response.data && (err.response.data.detail || err.response.data.message)) ||
          err?.message ||
          "Continue failed";
        setError(detail);
      } finally {
        if (continuationLockAcquired) {
          releaseToolContinuationLock(continuationLockKey);
        }
        setLoading(false);
        setIsStreaming(false);
        setActiveMessageId && setActiveMessageId(null);
      }
    },
  [
    abortActiveRequest,
    applySubchatControlFromTools,
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
      const tools = Array.isArray(toolsOverride) ? toolsOverride : msgBase.tools;
      const messageForContinuation = { ...msgBase, tools };
      if (!canContinueMessage(messageForContinuation)) return;
      const batch = buildToolContinuationBatch(tools);
      if (!batch) return;
      if (hasMatchingToolContinuationSignature(msgBase.metadata, batch)) return;
      const localLockKey = buildToolContinuationLockKey({
        sessionId: state.sessionId,
        messageId: msgBase.id,
        tools: batch,
      });
      if (!localLockKey || toolContinueLocksRef.current.has(localLockKey)) return;
      toolContinueLocksRef.current.add(localLockKey);
      try {
        await continueGenerating(
          { ...msgBase, tools: batch },
          continueTarget ? { continueTarget } : undefined,
        );
      } finally {
        toolContinueLocksRef.current.delete(localLockKey);
      }
    },
    [canContinueMessage, continueGenerating, state.sessionId],
  );

  useEffect(() => {
    if (loading || isStreaming) return;
    const conversation = Array.isArray(state.conversation) ? state.conversation : [];
    const candidate = conversation.find((entry) => {
      if (!entry || entry.role !== "ai") return false;
      const metadata =
        entry.metadata && typeof entry.metadata === "object" ? entry.metadata : {};
      return (
        (metadata.inline_tool_continuation_pending === true ||
          metadata.tool_result_continuation_pending === true) &&
        metadata.tool_response_pending === true &&
        Boolean(buildToolContinuationBatch(resolveMessageTools(entry)))
      );
    });
    if (!candidate) return;
    void maybeContinueAfterTools(candidate);
  }, [loading, isStreaming, maybeContinueAfterTools, state.conversation]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handleToolReviewContinue = (event) => {
      const detail = event?.detail || {};
      if (detail.handled) return;
      const action = normalizeToolReviewAction(detail.action);
      if (action !== "continue") return;
      const target = normalizeToolReviewTarget(detail);
      const conversation = Array.isArray(state.conversation) ? state.conversation : [];
      const targetIds = new Set(
        (target.scope === "batch"
          ? target.toolIds
          : [target.selectedToolId || target.toolId]
        )
          .filter(Boolean)
          .map(String),
      );
      const targetMessageId = target.messageId || target.chainId;
      const msg = conversation.find((entry) => {
        if (!entry || typeof entry !== "object") return false;
        if (targetMessageId && entry.id === targetMessageId) return true;
        if (!targetIds.size) return false;
        return resolveMessageTools(entry).some((tool) => {
          const rawId = tool?.id ?? tool?.request_id ?? null;
          return rawId ? targetIds.has(String(rawId)) : false;
        });
      });
      if (!msg) return;
      const messageTools = resolveMessageTools(msg);
      const scopedTools =
        target.scope !== "batch" && targetIds.size
          ? messageTools.filter((tool) => {
              const rawId = tool?.id ?? tool?.request_id ?? null;
              return rawId ? targetIds.has(String(rawId)) : false;
            })
          : messageTools;
      const batch = buildToolContinuationBatch(scopedTools);
      if (!batch) return;
      detail.handled = true;
      void maybeContinueAfterTools(msg, batch);
    };
    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewContinue);
    return () => {
      window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handleToolReviewContinue);
    };
  }, [maybeContinueAfterTools, state.conversation]);

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
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(() =>
            scrollMessageIntoView(chainId, "smooth", { block: "start" }),
          );
        } else {
          setTimeout(
            () => scrollMessageIntoView(chainId, "smooth", { block: "start" }),
            0,
          );
        }
        return;
      }
      if ((!canShowInline || !inlineToolsEnabled) && typeof onOpenConsole === "function") {
        onOpenConsole({
          toolId,
          chainId,
        });
      }
    },
    [
      inlineToolsEnabled,
      onOpenConsole,
      scrollMessageIntoView,
      setActiveMessageId,
      toolLinkBehavior,
    ],
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
      return <MermaidBlock code={code} key={`mermaid-${msg?.id || idx}`} />;
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
        `href="#"`,
        `data-tool-index="${toolIndex}"`,
        chainId ? `data-chain-id="${escapeHtml(chainId)}"` : "",
        toolId ? `data-tool-id="${escapeHtml(toolId)}"` : "",
        `aria-label="Open ${escapeHtml(label)}"`,
      ]
        .filter(Boolean)
        .join(" ");
      return `<a class="inline-tool-placeholder" ${attrs}>${escapeHtml(label)}</a>`;
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

  const splitFlattenedToolContinuationText = (value) => {
    const text = typeof value === "string" ? value.replace(/\r\n/g, "\n").trim() : "";
    if (!text) return null;
    const parts = text
      .split(
        /\n{2,}(?=(?:Recalling worked|Recall worked|Saved\b|Done\b|I (?:found|checked|retrieved|got|saved|created|updated)\b|The (?:tool|search|result)\b|Here(?:'s| is)\b|Based on (?:the )?(?:tool|results)\b))/i,
      )
      .map((part) => part.trim())
      .filter(Boolean);
    if (parts.length < 2) return null;
    return {
      prelude: parts[0],
      phases: parts.slice(1).map((part) => ({ text: part })),
    };
  };

  const getToolContinuationRenderState = (msg) => {
    if (!msg || (msg.role !== "ai" && msg.role !== "assistant")) return null;
    const metadata =
      msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
    let phases = normalizeToolContinuationPhases(metadata);
    const rawPrelude =
      typeof metadata.tool_prelude_text === "string"
        ? metadata.tool_prelude_text.trim()
        : "";
    if (phases.length) {
      return {
        prelude: rawPrelude && !isContinuationPlaceholderText(rawPrelude) ? rawPrelude : "",
        phases,
      };
    }
    if (
      metadata.tool_continued &&
      Array.isArray(msg.tools) &&
      msg.tools.length
    ) {
      const fallbackText =
        typeof msg.text === "string"
          ? msg.text.trim()
          : typeof msg.content === "string"
            ? msg.content.trim()
            : "";
      const split = splitFlattenedToolContinuationText(fallbackText);
      if (split?.phases?.length) {
        return split;
      }
    }
    return null;
  };

  const renderToolContinuationPhases = (msg, idx, toolsForRender, phaseState) => {
    if (!phaseState?.phases?.length) return null;
    const stepOffset = phaseState.prelude ? 2 : 1;
    return (
      <div className="tool-continuation-phase-stack">
        {phaseState.phases.map((phase, phaseIndex) => (
          <React.Fragment key={`${msg?.id || idx}-tool-phase-${phaseIndex}`}>
            <div className="tool-continuation-divider">
              <span>{`step ${phaseIndex + stepOffset}`}</span>
            </div>
            <div className="tool-continuation-phase">
              {renderContent(
                { ...msg, text: phase.text },
                `${idx}-tool-phase-${phaseIndex}`,
                toolsForRender,
              )}
            </div>
          </React.Fragment>
        ))}
      </div>
    );
  };

  const acceptCommandSuggestion = useCallback(
    (suggestion) => {
      if (!suggestion || !composerCommandContext) return;
      const before = message.slice(0, composerCommandContext.tokenStart);
      const after = message.slice(composerCommandContext.tokenEnd);
      const insertText = String(suggestion.insertText || "");
      placeComposerSelection(
        `${before}${insertText}${after}`,
        before.length + insertText.length,
      );
    },
    [composerCommandContext, message, placeComposerSelection],
  );

  const handleKeyDown = (e) => {
    const selectionStart =
      typeof e?.target?.selectionStart === "number" ? e.target.selectionStart : composerCursor;
    const selectionEnd =
      typeof e?.target?.selectionEnd === "number" ? e.target.selectionEnd : selectionStart;
    if (e.key === "Backspace" && selectionStart === selectionEnd && selectionStart > 0) {
      const unlinked = unlinkCommandText(message, selectionStart);
      if (unlinked) {
        e.preventDefault();
        placeComposerSelection(unlinked.text, unlinked.cursor);
        return;
      }
    }
    if (e.key === "ArrowDown" && commandSuggestions.length) {
      e.preventDefault();
      setActiveCommandSuggestionIndex((prev) =>
        prev >= commandSuggestions.length - 1 ? 0 : prev + 1,
      );
      return;
    }
    if (e.key === "ArrowUp" && commandSuggestions.length) {
      e.preventDefault();
      setActiveCommandSuggestionIndex((prev) =>
        prev <= 0 ? commandSuggestions.length - 1 : prev - 1,
      );
      return;
    }
    if (e.key === "Escape" && commandSuggestions.length) {
      e.preventDefault();
      setCommandSuggestions([]);
      setActiveCommandSuggestionIndex(0);
      return;
    }
    if (e.key === "Tab" && commandSuggestions.length && activeCommandSuggestion) {
      e.preventDefault();
      acceptCommandSuggestion(activeCommandSuggestion);
      return;
    }
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

  const uploadAndAttach = useCallback(async (file, options = {}) => {
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
    const id = options.attachmentId || createClientMessageId("attachment");
    const uploadSessionId = options.sessionId || state.sessionId;
    const uploadKey = `${uploadSessionId}:${id}`;
    if (activeAttachmentUploadsRef.current.has(uploadKey)) return;
    activeAttachmentUploadsRef.current.add(uploadKey);
    const existingAttachment = attachmentsRef.current.find(
      (attachment) =>
        attachment?.id === id ||
        attachment?.outboxId === id ||
        attachment?.outbox_id === id,
    );
    const url =
      options.previewUrl || existingAttachment?.url || createAttachmentPreviewUrl(file);
    const origin = options.origin || "upload";
    const captureSource = options.captureSource || null;
    const isTransientCapture = origin === "captured";
    removedAttachmentIdsRef.current.delete(id);
    const pendingAttachment = {
      ...(existingAttachment || {}),
      id,
      outboxId: id,
      outbox_id: id,
      file,
      name: file.name || options.name || "attachment",
      type: file.type || options.type || "",
      size: typeof file.size === "number" ? file.size : options.size,
      url,
      remoteUrl: null,
      uploading: true,
      uploadState: "uploading",
      uploadFailed: false,
      uploadError: "",
      contentHash: null,
      origin,
      capture_source: captureSource,
      transient: isTransientCapture,
    };
    setAttachments((prev) => {
      const index = prev.findIndex(
        (attachment) =>
          attachment?.id === id ||
          attachment?.outboxId === id ||
          attachment?.outbox_id === id,
      );
      const next = [...prev];
      if (index >= 0) {
        next[index] = { ...next[index], ...pendingAttachment };
      } else {
        next.push(pendingAttachment);
      }
      attachmentsRef.current = next;
      return next;
    });
    if (activeComposerSessionRef.current === uploadSessionId) {
      setError(null);
    }
    try {
      await putAttachmentOutboxEntry(uploadSessionId, {
        id,
        state: "uploading",
        file,
        name: pendingAttachment.name,
        type: pendingAttachment.type,
        size: pendingAttachment.size,
        lastModified: Number(file.lastModified) || Date.now(),
        origin,
        captureSource,
        transient: isTransientCapture,
        descriptor: null,
        error: "",
      });
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
      if (!remoteUrl) {
        throw new Error("Attachment upload completed without a file URL.");
      }
      const contentHash = res.data?.content_hash || null;
      const captureId = res.data?.capture_id || null;
      if (removedAttachmentIdsRef.current.has(id)) {
        await deleteAttachmentOutboxEntry(uploadSessionId, id);
        return;
      }
      const readyAttachment = {
        id,
        outboxId: id,
        outbox_id: id,
        name: file.name,
        type: file.type,
        size: file.size,
        url: remoteUrl,
        remoteUrl,
        contentHash,
        content_hash: contentHash,
        uploading: false,
        uploadState: "ready",
        uploadFailed: false,
        uploadError: "",
        origin: res.data?.origin || origin,
        relative_path: res.data?.relative_path || "",
        source_url: res.data?.source_url || "",
        source_url_recorded_at: res.data?.source_url_recorded_at || "",
        capture_source: captureSource,
        capture_id: captureId,
        transient:
          typeof res.data?.transient === "boolean"
            ? res.data.transient
            : isTransientCapture,
        promoted: res.data?.promoted === true,
        expires_at: res.data?.expires_at_iso || res.data?.expires_at || null,
        index_status: res.data?.index_status || null,
        caption_status: res.data?.caption_status || null,
      };
      const [storedDescriptor] = serializeComposerDraftAttachments([readyAttachment]);
      await putAttachmentOutboxEntry(uploadSessionId, {
        id,
        state: "ready",
        file: null,
        name: readyAttachment.name,
        type: readyAttachment.type,
        size: readyAttachment.size,
        origin: readyAttachment.origin,
        captureSource,
        transient: readyAttachment.transient,
        descriptor: storedDescriptor || readyAttachment,
        error: "",
      });
      if (removedAttachmentIdsRef.current.has(id)) {
        await deleteAttachmentOutboxEntry(uploadSessionId, id);
        return;
      }
      mergeStoredComposerDraftAttachment(uploadSessionId, readyAttachment);
      if (activeComposerSessionRef.current === uploadSessionId) {
        setAttachments((prev) => {
          const next = prev.map((attachment) =>
            attachment.id === id ||
            attachment.outboxId === id ||
            attachment.outbox_id === id
              ? { ...attachment, ...readyAttachment, file, url: attachment.url || url }
              : attachment,
          );
          attachmentsRef.current = next;
          return next;
        });
      }
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
      const detail = getRequestErrorDetail(err, "Attachment upload failed");
      if (!removedAttachmentIdsRef.current.has(id)) {
        await putAttachmentOutboxEntry(uploadSessionId, {
          id,
          state: "failed",
          file,
          name: pendingAttachment.name,
          type: pendingAttachment.type,
          size: pendingAttachment.size,
          lastModified: Number(file.lastModified) || Date.now(),
          origin,
          captureSource,
          transient: isTransientCapture,
          descriptor: null,
          error: detail,
        });
      }
      if (activeComposerSessionRef.current === uploadSessionId) {
        setError(detail);
        setAttachments((prev) => {
          const next = prev.map((attachment) =>
            attachment.id === id ||
            attachment.outboxId === id ||
            attachment.outbox_id === id
              ? {
                  ...attachment,
                  uploading: false,
                  uploadState: "failed",
                  uploadFailed: true,
                  uploadError: detail,
                }
              : attachment,
          );
          attachmentsRef.current = next;
          return next;
        });
      }
    } finally {
      activeAttachmentUploadsRef.current.delete(uploadKey);
    }
  }, [state.sessionId]);

  const retryAttachmentUpload = useCallback((attachment) => {
    const file = attachment?.file;
    const attachmentId =
      attachment?.outboxId || attachment?.outbox_id || attachment?.id;
    if (!file || !attachmentId) {
      setError("This attachment can no longer be retried. Remove it and select it again.");
      return;
    }
    void uploadAndAttach(file, {
      attachmentId,
      sessionId: state.sessionId,
      previewUrl: attachment.url || "",
      origin: attachment.origin || "upload",
      captureSource: attachment.capture_source || attachment.captureSource || null,
    });
  }, [state.sessionId, uploadAndAttach]);

  useEffect(() => {
    let cancelled = false;
    const sessionId = state.sessionId;

    const restoreOutbox = async () => {
      const entries = await listAttachmentOutboxEntries(sessionId);
      if (
        cancelled ||
        !entries.length ||
        activeComposerSessionRef.current !== sessionId
      ) {
        return;
      }
      const tombstonedAttachmentIds =
        readStoredComposerAttachmentTombstones(sessionId);

      const retryEntries = [];
      setAttachments((previous) => {
        const next = [...previous];
        for (const entry of entries) {
          if (
            removedAttachmentIdsRef.current.has(entry.id) ||
            tombstonedAttachmentIds.has(entry.id)
          ) {
            void deleteAttachmentOutboxEntry(sessionId, entry.id);
            continue;
          }
          let restored = null;
          if (entry.state === "ready" && entry.descriptor) {
            const [descriptor] = serializeComposerDraftAttachments([entry.descriptor]);
            if (descriptor) {
              restored = {
                ...descriptor,
                id: entry.id,
                outboxId: entry.id,
                outbox_id: entry.id,
                uploading: false,
                uploadState: "ready",
                uploadFailed: false,
                uploadError: "",
              };
            }
          } else {
            const file = fileFromAttachmentOutboxEntry(entry);
            if (!file) continue;
            const previewUrl = createAttachmentPreviewUrl(file);
            restored = {
              id: entry.id,
              outboxId: entry.id,
              outbox_id: entry.id,
              file,
              name: entry.name || file.name || "attachment",
              type: entry.type || file.type || "",
              size: typeof entry.size === "number" ? entry.size : file.size,
              url: previewUrl,
              remoteUrl: null,
              contentHash: null,
              uploading: false,
              uploadState: entry.state || "pending",
              uploadFailed: entry.state === "failed",
              uploadError: entry.error || "",
              origin: entry.origin || "upload",
              capture_source: entry.captureSource || null,
              transient: entry.transient === true,
            };
            retryEntries.push({ entry, file, previewUrl });
          }
          if (!restored) continue;

          const restoredRemote = restored.remoteUrl || restored.url || "";
          const existingIndex = next.findIndex((attachment) => {
            const attachmentOutboxId =
              attachment?.outboxId || attachment?.outbox_id || attachment?.id;
            const attachmentRemote = attachment?.remoteUrl || attachment?.url || "";
            return (
              attachmentOutboxId === entry.id ||
              (restoredRemote && attachmentRemote === restoredRemote)
            );
          });
          if (existingIndex >= 0) {
            if (
              restored.url &&
              restored.url.startsWith("blob:") &&
              next[existingIndex]?.url &&
              next[existingIndex].url !== restored.url &&
              next[existingIndex].url.startsWith("blob:")
            ) {
              try {
                URL.revokeObjectURL(next[existingIndex].url);
              } catch {
                // Releasing a stale preview is best-effort.
              }
            }
            next[existingIndex] = { ...next[existingIndex], ...restored };
          } else {
            next.push(restored);
          }
        }
        attachmentsRef.current = next;
        return next;
      });

      for (const { entry, file, previewUrl } of retryEntries) {
        if (cancelled || activeComposerSessionRef.current !== sessionId) break;
        if (removedAttachmentIdsRef.current.has(entry.id)) continue;
        void uploadAndAttach(file, {
          attachmentId: entry.id,
          sessionId,
          previewUrl,
          origin: entry.origin || "upload",
          captureSource: entry.captureSource || null,
        });
      }
    };

    void restoreOutbox();
    return () => {
      cancelled = true;
    };
  }, [state.sessionId, uploadAndAttach]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const retryFailedAttachments = () => {
      attachmentsRef.current
        .filter((attachment) => attachment?.uploadFailed && attachment?.file)
        .forEach(retryAttachmentUpload);
    };
    window.addEventListener("online", retryFailedAttachments);
    return () => window.removeEventListener("online", retryFailedAttachments);
  }, [retryAttachmentUpload]);

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
    removedAttachmentIdsRef.current.add(id);
    const found = attachmentsRef.current.find(
      (attachment) =>
        attachment?.id === id ||
        attachment?.outboxId === id ||
        attachment?.outbox_id === id,
    );
    if (found) revokeAttachmentPreview(found);
    const outboxId = found?.outboxId || found?.outbox_id || found?.id || id;
    removedAttachmentIdsRef.current.add(outboxId);
    removeStoredComposerDraftAttachments(state.sessionId, [
      found || { outboxId },
    ]);
    markStoredComposerAttachmentTombstones(state.sessionId, [outboxId]);
    const next = attachmentsRef.current.filter(
      (attachment) =>
        attachment?.id !== id &&
        attachment?.outboxId !== id &&
        attachment?.outbox_id !== id,
    );
    attachmentsRef.current = next;
    setAttachments(next);
    void deleteAttachmentOutboxEntry(state.sessionId, outboxId);
  };

  const handleAudioStop = async () => {
    const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
    if (!blob.size) {
      setError("No audio was recorded.");
      return;
    }
    const formData = new FormData();
    formData.append("file", blob, "recording.webm");
    if (selectedSttModel) {
      formData.append("model", selectedSttModel);
    }
    setAudioTranscribing(true);
    try {
      const res = await axios.post("/api/voice/transcribe", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const text = String(res?.data?.text || "").trim();
      if (!text) {
        setError("Speech-to-text returned no transcript.");
        return;
      }
      setError(null);
      setMessage(text);
      await sendMessage(text);
    } catch (err) {
      console.error("Audio upload failed", err);
      setError(getRequestErrorDetail(err, "Audio transcription failed"));
    } finally {
      setAudioTranscribing(false);
    }
  };

  const stopAudioRecorder = useCallback(({ process = true } = {}) => {
    const recorder = mediaRecorderRef.current;
    if (!process) {
      audioChunksRef.current = [];
    }
    if (!recorder) {
      setAudioRecording(false);
      return;
    }
    if (!process) {
      recorder.onstop = null;
    }
    try {
      if (recorder.state !== "inactive") {
        recorder.stop();
      }
    } catch (_) {}
    try {
      recorder.stream?.getTracks?.().forEach((track) => track.stop());
    } catch (_) {}
    mediaRecorderRef.current = null;
    setAudioRecording(false);
  }, []);

  const toggleAudioRecording = async () => {
    if (audioRecording) {
      stopAudioRecorder({ process: true });
      return;
    }
    if (audioTranscribing) return;
    if (liveStreamingActive) {
      setError("Stop live streaming mode before recording an audio message.");
      return;
    }
    if (state.backendMode === "api" && state.apiStatus !== "online") {
      setError("API not ready");
      return;
    }
    try {
      setError(null);
      stopMicTest();
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
    if (audioRecording || mediaRecorderRef.current) {
      stopAudioRecorder({ process: false });
    }
    const attemptId = liveSessionAttemptRef.current + 1;
    liveSessionAttemptRef.current = attemptId;
    stopMicTest();
    setChatSettingsOpen(false);
    try {
      setError(null);
      setBanner(null);
      setLiveSessionPending(true);
      setLiveStreamingPhase("connecting");
      setLiveStreamingTranscript({ user: "", assistant: "" });
      const res = await axios.post("/api/voice/connect", {
        identity: createClientMessageId("voice"),
        room: "float",
        workflow: workflowProfile,
        ...thinkingPayload,
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
      if (
        session.transport === "local-bridge" ||
        session.provider === "float-local-live"
      ) {
        throw new Error(
          session.detail ||
            "Local live bridge is selected, but browser duplex audio is not wired yet.",
        );
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
  const hasUnreadyAttachments = attachments.some((att) => !att?.remoteUrl);
  const hasDraftText = Boolean(message && message.trim());
  const hasSendableAttachments = attachments.some((att) => Boolean(att?.remoteUrl));
  const compareNeedsMoreImages =
    hasImageAttachments && visionWorkflow === "compare" && imageAttachmentCount < 2;
  const sendDisabled = isStreaming
    ? false
    : loading ||
      (!hasDraftText && !hasSendableAttachments) ||
      hasUnreadyAttachments ||
      compareNeedsMoreImages;
  const sendTooltip = isStreaming
    ? "Stop generation"
    : hasUploadingAttachments
      ? "Attachments are still uploading"
      : hasUnreadyAttachments
        ? "Retry or remove failed attachments"
      : compareNeedsMoreImages
        ? "Compare mode needs at least two images"
        : !hasDraftText && hasSendableAttachments
          ? "Send attachments"
      : hasDraftText
        ? "Send message"
        : "Type a message to send";
  const conversationMessages = Array.isArray(state.conversation)
    ? state.conversation
    : EMPTY_CONVERSATION;
  const stateConversationWindowMeta =
    state.conversationTrimMeta?.truncated ? state.conversationTrimMeta : null;
  const renderConversationWindow = useMemo(() => {
    if (!conversationMessages.length) {
      return {
        messages: conversationMessages,
        startIndex: 0,
        meta: stateConversationWindowMeta,
      };
    }
    if (stateConversationWindowMeta) {
      const startIndex = Number.isFinite(Number(stateConversationWindowMeta.start_index))
        ? Number(stateConversationWindowMeta.start_index)
        : 0;
      return {
        messages: conversationMessages,
        startIndex,
        meta: stateConversationWindowMeta,
      };
    }
    const messageLimit = getConversationMessageLimit();
    if (conversationMessages.length <= messageLimit) {
      return {
        messages: conversationMessages,
        startIndex: 0,
        meta: null,
      };
    }
    const startIndex = Math.max(0, conversationMessages.length - messageLimit);
    return {
      messages: conversationMessages.slice(startIndex),
      startIndex,
      meta: {
        truncated: true,
        source: "render",
        render_only: true,
        total_messages: conversationMessages.length,
        omitted_messages: startIndex,
        start_index: startIndex,
        message_limit: messageLimit,
        omitted_tools: 0,
      },
    };
  }, [conversationMessages, stateConversationWindowMeta]);
  const visibleConversationMessages = renderConversationWindow.messages;
  const renderedConversationMessages = visibleConversationMessages.length;
  const rawConversationWindowMeta = renderConversationWindow.meta;
  const visibleWindowStart = Number.isFinite(Number(rawConversationWindowMeta?.start_index))
    ? Number(rawConversationWindowMeta.start_index)
    : renderConversationWindow.startIndex;
  const recordedConversationTotal = Number.isFinite(
    Number(rawConversationWindowMeta?.total_messages),
  )
    ? Number(rawConversationWindowMeta.total_messages)
    : renderedConversationMessages;
  const totalConversationMessages = Math.max(
    recordedConversationTotal,
    visibleWindowStart + renderedConversationMessages,
  );
  const hasMessageWindow =
    Boolean(rawConversationWindowMeta) &&
    (totalConversationMessages > renderedConversationMessages ||
      Number(rawConversationWindowMeta?.omitted_messages) > 0);
  const conversationWindowMeta = hasMessageWindow ? rawConversationWindowMeta : null;
  const omittedConversationMessages = Number.isFinite(
    Number(conversationWindowMeta?.omitted_messages),
  )
    ? Number(conversationWindowMeta.omitted_messages)
    : Math.max(0, totalConversationMessages - renderedConversationMessages);
  const suggestedKeepLast = Number.isFinite(
    Number(compactionSuggestion?.recommended_keep_last),
  )
    ? Math.max(1, Math.min(200, Number(compactionSuggestion.recommended_keep_last)))
    : null;
  const suggestedSummaryChars = Number.isFinite(
    Number(compactionSuggestion?.recommended_summary_chars),
  )
    ? Math.max(
        500,
        Math.min(20000, Number(compactionSuggestion.recommended_summary_chars)),
      )
    : null;
  const contextWindowTokens = Number.isFinite(Number(state.maxContextLength))
    ? Math.max(1024, Number(state.maxContextLength))
    : null;
  const compactionKeepLast = Math.min(
    200,
    Math.max(
      1,
      Math.min(suggestedKeepLast ?? (renderedConversationMessages || 40), 40),
    ),
  );
  const formatCompactionCount = useCallback((value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value ?? "");
  }, []);
  const formatCompactionElapsed = useCallback((value) => {
    const ms = Math.max(0, Number(value) || 0);
    if (ms < 1000) return `${Math.round(ms)} ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }, []);
  const isContextOverflowDetail = useCallback((value) => {
    const text = String(value || "").trim().toLowerCase();
    if (!text) return false;
    return [
      "context length",
      "context window",
      "maximum context",
      "max context",
      "too many tokens",
      "token limit",
      "prompt is too long",
      "reduce the length",
    ].some((pattern) => text.includes(pattern));
  }, []);
  const buildCompactionPayload = useCallback(
    () => {
      const payload = {
        conversation_id: state.sessionId,
        keep_last: compactionKeepLast,
        max_summary_chars: suggestedSummaryChars ?? 6000,
        summary_mode: compactionSummaryMode,
      };
      if (contextWindowTokens) {
        payload.context_window_tokens = contextWindowTokens;
      }
      return payload;
    },
    [
      compactionKeepLast,
      compactionSummaryMode,
      contextWindowTokens,
      state.sessionId,
      suggestedSummaryChars,
    ],
  );
  const planConversationCompaction = useCallback(async () => {
    if (!state.sessionId || compactionBusy) return null;
    const startedAt = Date.now();
    setCompactionBusy(true);
    setCompactionError("");
    setCompactionStartedAt(startedAt);
    setCompactionPhaseLabel("Estimating conversation context budget");
    setCompactionElapsedMs(null);
    try {
      const response = await axios.post(
        "/api/conversations/compact/plan",
        buildCompactionPayload(),
      );
      const payload = response?.data || null;
      setCompactionSuggestion(payload);
      if (payload) {
        setCompactionStatus(
          `Context budget ${String(payload.status || "pressure").replace(/_/g, " ")}. Keep ${payload.recommended_keep_last || "recent"} messages and summarize the rest.`,
        );
      }
      setCompactionElapsedMs(Date.now() - startedAt);
      return payload;
    } catch (err) {
      console.error("Conversation compaction planning failed", err);
      setCompactionError(
        getRequestErrorDetail(err, "Conversation compaction planning failed."),
      );
      setCompactionElapsedMs(Date.now() - startedAt);
      return null;
    } finally {
      setCompactionBusy(false);
      setCompactionPhaseLabel("");
    }
  }, [buildCompactionPayload, compactionBusy, state.sessionId]);
  const previewConversationCompaction = useCallback(async () => {
    if (!state.sessionId || compactionBusy) return;
    const startedAt = Date.now();
    setCompactionBusy(true);
    setCompactionError("");
    setCompactionStatus("");
    setCompactionStartedAt(startedAt);
    setCompactionPhaseLabel("Previewing compacted conversation context");
    setCompactionElapsedMs(null);
    try {
      const response = await axios.post(
        "/api/conversations/compact/preview",
        buildCompactionPayload(),
      );
      const payload = response?.data || {};
      setCompactionPreview(payload);
      if (payload?.budget_plan) {
        setCompactionSuggestion(payload.budget_plan);
      }
      const method = payload.summary_method || payload.summary_mode || compactionSummaryMode;
      setCompactionStatus(`Preview ready (${method}).`);
      setCompactionElapsedMs(
        Number.isFinite(Number(payload?.elapsed_ms))
          ? Number(payload.elapsed_ms)
          : Date.now() - startedAt,
      );
    } catch (err) {
      console.error("Conversation compaction preview failed", err);
      setCompactionError(
        getRequestErrorDetail(err, "Conversation compaction preview failed."),
      );
      setCompactionElapsedMs(Date.now() - startedAt);
    } finally {
      setCompactionBusy(false);
      setCompactionPhaseLabel("");
    }
  }, [buildCompactionPayload, compactionBusy, compactionSummaryMode, state.sessionId]);
  const writeConversationCompaction = useCallback(async () => {
    if (!state.sessionId || compactionBusy) return;
    const startedAt = Date.now();
    setCompactionBusy(true);
    setCompactionError("");
    setCompactionStatus("");
    setCompactionStartedAt(startedAt);
    setCompactionPhaseLabel("Creating a compacted conversation copy");
    setCompactionElapsedMs(null);
    try {
      const response = await axios.post("/api/conversations/compact/write", {
        ...buildCompactionPayload(),
        target_conversation_id: "",
        replace: false,
      });
      const payload = response?.data || {};
      setCompactionPreview(null);
      setCompactionSuggestion(null);
      setCompactionStatus(
        `Created compacted copy: ${payload.target_conversation_id || "new conversation"}.`,
      );
      setCompactionElapsedMs(
        Number.isFinite(Number(payload?.elapsed_ms))
          ? Number(payload.elapsed_ms)
          : Date.now() - startedAt,
      );
    } catch (err) {
      console.error("Conversation compaction write failed", err);
      setCompactionError(
        getRequestErrorDetail(err, "Conversation compaction write failed."),
      );
      setCompactionElapsedMs(Date.now() - startedAt);
    } finally {
      setCompactionBusy(false);
      setCompactionPhaseLabel("");
    }
  }, [buildCompactionPayload, compactionBusy, state.sessionId]);
  const liveStreamingStatusLabel = getLiveStreamingStatusLabel(
    liveStreamingPhase,
  );
  const showCompactionNotice = Boolean(conversationWindowMeta || compactionSuggestion);
  const activeCompactionElapsedMs =
    compactionBusy && compactionStartedAt
      ? Math.max(0, compactionNowMs - compactionStartedAt)
      : null;
  const audioRecorderStatus = audioRecording
    ? "Recording microphone input"
    : audioTranscribing
      ? "Transcribing microphone input"
      : "";
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
  const renderAudioModelSelect = (id, value, options, onChange, fallbackLane) => {
    const selectedValue = String(value || "").trim();
    const selectedOption = options.find((option) => option.value === selectedValue);
    const lane = selectedOption?.lane || inferAudioModelLane(selectedValue, fallbackLane);
    const allOptions = selectedOption
      ? options
      : selectedValue
        ? [{ value: selectedValue, label: selectedValue, lane }, ...options]
        : options;
    return (
      <div className={`chat-settings-model-select model-lane-${lane}`}>
        <span className="model-lane-pip" aria-hidden="true" />
        <select id={id} value={selectedValue} onChange={onChange}>
          {allOptions.map((option) => (
            <option key={option.value} value={option.value} data-lane={option.lane}>
              {optionLabelWithLane(option)}
            </option>
          ))}
        </select>
      </div>
    );
  };
  const chatSettingsPopover =
    chatSettingsOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={chatSettingsPopoverRef}
            id="chat-settings-popover"
            className="chat-settings-popover"
            role="dialog"
            aria-label="Chat settings"
            style={chatSettingsPopoverStyle || { visibility: "hidden" }}
          >
            <header className="chat-settings-header">
              <div>
                <strong>Chat settings</strong>
                <span>Controls for this composer and active chat.</span>
              </div>
              <button
                type="button"
                className="chat-settings-close"
                aria-label="Close chat settings"
                onClick={() => {
                  setChatSettingsOpen(false);
                  window.requestAnimationFrame(() =>
                    chatSettingsTriggerRef.current?.focus(),
                  );
                }}
              >
                <CloseIcon fontSize="small" />
              </button>
            </header>
            <div className="chat-settings-body">
            <div className="chat-settings-list" role="tablist" aria-label="Chat setting sections">
              {CHAT_SETTINGS_SECTIONS.map(([key, label], index) => (
                <button
                  key={key}
                  id={`chat-settings-tab-${key}`}
                  type="button"
                  role="tab"
                  aria-selected={chatSettingsSection === key}
                  aria-controls="chat-settings-active-panel"
                  tabIndex={chatSettingsSection === key ? 0 : -1}
                  className={`chat-settings-item${
                    chatSettingsSection === key ? " is-active" : ""
                  }`}
                  onClick={() => setChatSettingsSection(key)}
                  onKeyDown={(event) => {
                    let nextIndex = null;
                    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
                      nextIndex = (index + 1) % CHAT_SETTINGS_SECTIONS.length;
                    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
                      nextIndex =
                        (index - 1 + CHAT_SETTINGS_SECTIONS.length) %
                        CHAT_SETTINGS_SECTIONS.length;
                    } else if (event.key === "Home") {
                      nextIndex = 0;
                    } else if (event.key === "End") {
                      nextIndex = CHAT_SETTINGS_SECTIONS.length - 1;
                    }
                    if (nextIndex === null) return;
                    event.preventDefault();
                    const [nextSection] = CHAT_SETTINGS_SECTIONS[nextIndex];
                    setChatSettingsSection(nextSection);
                    window.requestAnimationFrame(() =>
                      document.getElementById(`chat-settings-tab-${nextSection}`)?.focus(),
                    );
                  }}
                >
                  <span>{label}</span>
                  <KeyboardArrowRightIcon fontSize="inherit" />
                </button>
              ))}
            </div>
            <div
              id="chat-settings-active-panel"
              className="chat-settings-panel"
              role="tabpanel"
              aria-labelledby={`chat-settings-tab-${chatSettingsSection}`}
              tabIndex={0}
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
              {chatSettingsSection === "microphone" && (
                <>
                  <label htmlFor="chat-mic-input">microphone input</label>
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
                  <label htmlFor="chat-stt-model">STT model</label>
                  {renderAudioModelSelect(
                    "chat-stt-model",
                    selectedSttModel,
                    AUDIO_STT_MODEL_OPTIONS,
                    (event) =>
                      setState((prev) => ({
                        ...prev,
                        sttModel: event.target.value,
                      })),
                    "api",
                  )}
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
                  <label htmlFor="chat-mic-gain">microphone level</label>
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
                </>
              )}
              {chatSettingsSection === "volume" && (
                <>
                  <label htmlFor="chat-tts-model">TTS model</label>
                  {renderAudioModelSelect(
                    "chat-tts-model",
                    selectedTtsModel,
                    AUDIO_TTS_MODEL_OPTIONS,
                    (event) =>
                      setState((prev) => ({
                        ...prev,
                        ttsModel: event.target.value,
                        voiceModel: defaultVoiceForTtsModel(
                          event.target.value,
                          prev.voiceModel,
                        ),
                      })),
                    "api",
                  )}
                  <label htmlFor="chat-tts-voice">TTS voice</label>
                  <select
                    id="chat-tts-voice"
                    value={
                      selectedTtsVoiceOptions.includes(selectedTtsVoice)
                        ? selectedTtsVoice
                        : ""
                    }
                    onChange={(event) =>
                      setState((prev) => ({
                        ...prev,
                        voiceModel: event.target.value,
                      }))
                    }
                  >
                    {!selectedTtsVoiceOptions.includes(selectedTtsVoice) && (
                      <option value="">
                        {selectedTtsVoice
                          ? `${selectedTtsVoice} (not valid for selected TTS)`
                          : "default voice"}
                      </option>
                    )}
                    {selectedTtsVoiceOptions.map((voice) => (
                      <option key={voice} value={voice}>
                        {voice}
                      </option>
                    ))}
                  </select>
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
                  <div className="chat-settings-compact-section">
                    <div className="chat-settings-label-row">
                      <label>thinking mode</label>
                      <Tooltip
                        title={REASONING_EFFORT_TOOLTIP_TEXT}
                        placement="top"
                        arrow
                      >
                        <button
                          type="button"
                          className="chat-settings-help"
                          aria-label="About reasoning effort"
                        >
                          ?
                        </button>
                      </Tooltip>
                    </div>
                    <div className="chat-settings-choice-row">
                      {[
                        { id: "auto", label: "auto" },
                        ...REASONING_EFFORT_PRESETS,
                      ].map((preset) => (
                        <button
                          key={preset.id}
                          type="button"
                          className={`chat-settings-choice${
                            thinkingMode === preset.id ? " is-active" : ""
                          }`}
                          onClick={() => setThinkingMode(preset.id)}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                    <div className="chat-settings-label-row">
                      <label htmlFor="chat-reasoning-effort">effort</label>
                      <span className="chat-settings-slider-value">
                        {customThinkingEffort
                          ? thinkingEffortValue.toFixed(2)
                          : thinkingMode === "auto"
                            ? "provider default"
                            : `${thinkingMode} · ${thinkingEffortValue.toFixed(2)}`}
                      </span>
                    </div>
                    <input
                      id="chat-reasoning-effort"
                      aria-label="reasoning effort"
                      type="range"
                      min="0"
                      max="0.99"
                      step="0.01"
                      value={thinkingEffortValue}
                      onChange={(event) => setThinkingMode(event.target.value)}
                    />
                  </div>

                  <div className="chat-settings-compact-section">
                    <div className="chat-settings-label-row">
                      <span className="chat-settings-label-with-help">
                        <label htmlFor="chat-max-output-tokens">response limit</label>
                        <Tooltip title={outputLimitTooltipText} placement="top" arrow>
                          <button
                            type="button"
                            className="chat-settings-help"
                            aria-label="About response limit"
                          >
                            ?
                          </button>
                        </Tooltip>
                      </span>
                      <span className="chat-settings-slider-value">
                        {outputTokenLimit
                          ? `${formatTokenLimit(outputTokenLimit)} tokens`
                          : "Auto"}
                      </span>
                    </div>
                    <select
                      id="chat-max-output-tokens"
                      aria-label="response limit"
                      value={outputTokenMode}
                      onChange={(event) => setOutputTokenMode(event.target.value)}
                    >
                      <option value="auto">auto (provider default)</option>
                      {OUTPUT_TOKEN_PRESETS.map((preset) => (
                        <option key={preset.id} value={preset.id}>
                          {preset.label}
                        </option>
                      ))}
                      <option value="custom">custom</option>
                    </select>
                    {outputTokenMode === "custom" && (
                      <input
                        aria-label="custom response limit"
                        type="number"
                        min="1"
                        max={MAX_CUSTOM_OUTPUT_TOKENS}
                        step="1024"
                        value={customOutputTokens}
                        onChange={(event) => setCustomOutputTokens(event.target.value)}
                      />
                    )}
                    <span className="chat-settings-capacity">
                      {formatTokenLimit(activeModelCapabilities?.maxContextLength)} context
                      {" · response max "}
                      {formatTokenLimit(activeModelCapabilities?.maxOutputTokens)}
                    </span>
                    {outputLimitWarning && (
                      <span className="chat-settings-warning" role="alert">
                        {outputLimitWarning}
                      </span>
                    )}
                  </div>

                  <div className="chat-settings-compact-section">
                    <div className="chat-settings-label-row">
                      <label>memory</label>
                      <Tooltip title={RAG_TOOLTIP_TEXT} placement="top" arrow>
                        <button
                          type="button"
                          className="chat-settings-help"
                          aria-label="About memory retrieval"
                        >
                          ?
                        </button>
                      </Tooltip>
                    </div>
                    <div className="chat-settings-toggle-stack">
                      <div
                        className={`chat-settings-rag-card${
                          textRagEnabled ? " is-enabled" : ""
                        }`}
                      >
                        <label className="chat-settings-rag-toggle">
                          <input
                            type="checkbox"
                            checked={textRagEnabled}
                            onChange={(event) =>
                              setRagEnabled("textRagEnabled", event.target.checked)
                            }
                          />
                          <strong>text models</strong>
                        </label>
                        <select
                          aria-label="text retrieval model"
                          title={`Text retrieval model: ${ragEmbeddingModel}`}
                          value={ragEmbeddingModel}
                          disabled={!textRagEnabled}
                          onChange={(event) =>
                            setRagModel("rag_embedding_model", event.target.value)
                          }
                        >
                          {RAG_TEXT_MODEL_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {optionLabelWithLane(option)}
                            </option>
                          ))}
                          {!RAG_TEXT_MODEL_OPTIONS.some(
                            (option) => option.value === ragEmbeddingModel,
                          ) && (
                            <option value={ragEmbeddingModel}>{ragEmbeddingModel}</option>
                          )}
                        </select>
                      </div>

                      <div
                        className={`chat-settings-rag-card${
                          visionRagEnabled ? " is-enabled" : ""
                        }`}
                      >
                        <label className="chat-settings-rag-toggle">
                          <input
                            type="checkbox"
                            checked={visionRagEnabled}
                            onChange={(event) =>
                              setRagEnabled("visionRagEnabled", event.target.checked)
                            }
                          />
                          <strong>vision models</strong>
                        </label>
                        <select
                          aria-label="vision retrieval model"
                          title={`Vision retrieval model: ${ragClipModel}`}
                          value={ragClipModel}
                          disabled={!visionRagEnabled}
                          onChange={(event) =>
                            setRagModel("rag_clip_model", event.target.value)
                          }
                        >
                          {RAG_VISION_MODEL_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {optionLabelWithLane(option)}
                            </option>
                          ))}
                          {!RAG_VISION_MODEL_OPTIONS.some(
                            (option) => option.value === ragClipModel,
                          ) && <option value={ragClipModel}>{ragClipModel}</option>}
                        </select>
                      </div>
                    </div>
                  </div>
                </>
              )}
              {chatSettingsSection === "workflow" && (
                <>
                  <label htmlFor="chat-workflow-profile">workflow for new turns</label>
                  <select
                    id="chat-workflow-profile"
                    value={workflowProfile}
                    onChange={(event) => setWorkflowProfile(event.target.value)}
                  >
                    {workflowOptions.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.label}
                      </option>
                    ))}
                  </select>
                  <span className="chat-settings-note">
                    {activeWorkflowProfile?.description || "Select how this chat should approach the work."}
                  </span>
                  <span className="chat-settings-note">
                    {Array.isArray(state.enabledWorkflowModules)
                      ? state.enabledWorkflowModules.length
                      : 0}{" "}
                    optional {state.enabledWorkflowModules?.length === 1 ? "module" : "modules"} enabled.
                  </span>
                  <span className="chat-settings-note">
                    Changing this also updates your default across chats. Profiles set guidance and reasoning; they do not launch workers or change global tool access.
                  </span>
                  <button
                    type="button"
                    className="chat-settings-link"
                    onClick={openWorkflowSettings}
                  >
                    Manage workflows &amp; modules in Knowledge
                  </button>
                </>
              )}
            </div>
            </div>
          </div>,
          document.body,
        )
      : null;
  const commandSuggestionsPopover =
    composerCommandContext &&
    (commandSuggestionsLoading || commandSuggestions.length > 0) &&
    commandMenuStyle &&
    typeof document !== "undefined"
      ? createPortal(
          <div
            className="composer-command-menu composer-command-menu-floating"
            role="listbox"
            aria-label="Command suggestions"
            style={commandMenuStyle}
          >
            {commandSuggestionsLoading && commandSuggestions.length === 0 ? (
              <div className="composer-command-empty">loading suggestions...</div>
            ) : (
              commandSuggestions.map((suggestion, index) => (
                <button
                  key={`${suggestion.kind}-${suggestion.label}-${index}`}
                  type="button"
                  ref={index === activeCommandSuggestionIndex ? activeCommandOptionRef : null}
                  role="option"
                  aria-selected={index === activeCommandSuggestionIndex}
                  className={`composer-command-option${
                    index === activeCommandSuggestionIndex ? " is-active" : ""
                  }`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    acceptCommandSuggestion(suggestion);
                  }}
                >
                  <span className={`composer-command-kind kind-${suggestion.kind}`}>
                    {suggestion.kind}
                  </span>
                  <span className="composer-command-copy">
                    <span className="composer-command-label">{suggestion.label}</span>
                    {suggestion.description ? (
                      <span className="composer-command-description">
                        {suggestion.description}
                      </span>
                    ) : null}
                  </span>
                </button>
              ))
            )}
          </div>,
          document.body,
        )
      : null;
  const inputAlertsFallbackStyle = buildComposerOverlayStyle({
    anchorRect: inputBoxRef.current?.getBoundingClientRect(),
    popoverRect: {
      width: Math.min(inputBoxRef.current?.getBoundingClientRect()?.width || 760, 980),
      height: Math.max(56, inputAlerts.length * 64),
    },
    maxWidth: 980,
    gap: 10,
    zIndex: 3620,
  });
  const attachmentPopoverFallbackStyle = buildComposerOverlayStyle({
    anchorRect: attachmentTriggerRef.current?.getBoundingClientRect(),
    popoverRect: { width: 145, height: 48 },
    maxWidth: 240,
    gap: 8,
    zIndex: 3610,
  });
  const inputAlertsPopover =
    entryOpen && inputAlerts.length > 0 && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={inputAlertStackRef}
            className="input-alert-stack input-alert-stack-floating"
            aria-live="polite"
            aria-label="Composer notices"
            style={inputAlertStyle || inputAlertsFallbackStyle}
          >
            {inputAlerts.map((message, index) => (
              <div
                key={`input-alert-${index}-${message}`}
                className="input-error"
                role="alert"
              >
                {message}
              </div>
            ))}
          </div>,
          document.body,
        )
      : null;
  return (
    <>
    <div className="chat-container" ref={chatContainerRef}>
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
      {parentConversationLink?.conversationId && (
        <button
          type="button"
          className="subchat-return-btn"
          onClick={() =>
            onOpenConversation?.(
              parentConversationLink.conversationId,
              parentConversationLink.label,
            )
          }
          title={`Return to ${parentConversationLink.label || "main chat"}`}
        >
          back to main chat
        </button>
      )}
      {showCompactionNotice &&
        typeof document !== "undefined" &&
        createPortal(
          <div className="conversation-compaction-notice" aria-live="polite">
          <div className="conversation-compaction-copy">
            <strong>
              {conversationWindowMeta ? "Full transcript saved." : "Context budget pressure detected."}
            </strong>
            {conversationWindowMeta ? (
              <>
                <span>
                  Rendering latest {renderedConversationMessages} of{" "}
                  {totalConversationMessages} messages.
                </span>
                {omittedConversationMessages > 0 && (
                  <span>{omittedConversationMessages} earlier messages stay out of the DOM.</span>
                )}
              </>
            ) : (
              <>
                <span>
                  Estimated history {formatCompactionCount(compactionSuggestion?.estimated_history_tokens)} of{" "}
                  {formatCompactionCount(compactionSuggestion?.usable_history_tokens)} usable tokens.
                </span>
                <span>
                  Recommendation: keep latest {formatCompactionCount(compactionSuggestion?.recommended_keep_last)} messages and summarize the rest.
                </span>
              </>
            )}
          </div>
          <div className="conversation-compaction-actions">
            <label className="conversation-compaction-mode">
              <span>summary</span>
              <select
                value={compactionSummaryMode}
                onChange={(event) => setCompactionSummaryMode(event.target.value)}
                disabled={compactionBusy}
                aria-label="Conversation compaction summary mode"
              >
                <option value="deterministic">deterministic</option>
                <option value="llm">LLM</option>
              </select>
            </label>
            <button
              type="button"
              className="chip"
              onClick={previewConversationCompaction}
              disabled={compactionBusy || !state.sessionId}
            >
              {compactionBusy ? "working..." : "preview compaction"}
            </button>
            <button
              type="button"
              className="chip"
              onClick={writeConversationCompaction}
              disabled={compactionBusy || !state.sessionId}
            >
              create compacted copy
            </button>
          </div>
          {compactionBusy && (
            <div className="conversation-compaction-progress" role="status" aria-live="polite">
              <div className="download-progress-track small conversation-compaction-progress-track">
                <div className="download-progress-fill conversation-compaction-progress-fill is-indeterminate" />
              </div>
              <span>
                {compactionPhaseLabel || "Working on conversation compaction."}
                {activeCompactionElapsedMs !== null
                  ? ` | ${formatCompactionElapsed(activeCompactionElapsedMs)}`
                  : ""}
              </span>
            </div>
          )}
          {(compactionStatus ||
            compactionError ||
            compactionPreview?.summary_preview ||
            compactionElapsedMs !== null) && (
            <div
              className={`conversation-compaction-result${
                compactionError ? " is-error" : ""
              }`}
            >
              {compactionError || compactionStatus}
              {compactionElapsedMs !== null && !compactionBusy && (
                <span className="conversation-compaction-meta">
                  elapsed {formatCompactionElapsed(compactionElapsedMs)}
                </span>
              )}
              {compactionPreview && (
                <span className="conversation-compaction-meta">
                  omitted {formatCompactionCount(compactionPreview.omitted_messages)} | retained{" "}
                  {formatCompactionCount(compactionPreview.retained_messages)}
                </span>
              )}
              {compactionPreview?.summary_preview && (
                <pre>{compactionPreview.summary_preview}</pre>
              )}
              {compactionPreview?.fallback_reason && (
                <span>Fallback: {compactionPreview.fallback_reason}</span>
              )}
            </div>
          )}
          </div>,
          document.body,
        )}
      {liveStreamingIndicator}
      <div className="chat-box" ref={chatBoxRef}>
        {conversationMessages.length === 0 && (
          <p className="placeholder">Start chatting!</p>
        )}
        {visibleConversationMessages.map((msg, visibleIdx) => {
          const idx = visibleWindowStart + visibleIdx;
          const isAssistantMessage = msg?.role === "ai" || msg?.role === "assistant";
          const isRegeneratingMessage = Boolean(
            isAssistantMessage &&
            msg?.id &&
            regeneratingMessageId === msg.id,
          );
          const displayMsg = isRegeneratingMessage
            ? {
                ...msg,
                text: "",
                content: "",
                thoughts: [],
                tools: [],
                ragMatches: [],
                rag: [],
                metadata: mergeAssistantMessageMetadata(msg?.metadata, {
                  status: "regenerating",
                  tool_response_pending: false,
                  tool_continued: false,
                  tool_continuation_phases: [],
                  tool_continuation_text: "",
                  tool_prelude_text: "",
                }),
              }
            : msg;
          const ragMatches = isRegeneratingMessage ? [] : getMessageRagMatches(displayMsg);
          const fragmentKey = msg && msg.id ? msg.id : idx;
          const previousTimestamp =
            visibleIdx > 0 ? visibleConversationMessages[visibleIdx - 1]?.timestamp : null;
          const timestampLabel = msg?.timestamp
            ? formatMessageTimestampLabel(msg.timestamp, previousTimestamp)
            : "";
          const timestampTitle = msg?.timestamp ? formatMessageTimestampTitle(msg.timestamp) : "";
          const isActiveMessage = msg && msg.id && msg.id === activeMessageId;
          const thoughtBlocks =
            isActiveMessage && !isRegeneratingMessage
              ? buildThoughtBlocks(displayMsg.thoughts)
              : [];
          const resolvedTools = isRegeneratingMessage ? [] : resolveMessageTools(displayMsg);
          const messageSourceLabel =
            isAssistantMessage ? getMessageSourceLabel(displayMsg) : "";
          const messageStatusBadge =
            isAssistantMessage
              ? getMessageStatusBadge(displayMsg)
              : null;
          const toolContinuationRenderState = isRegeneratingMessage
            ? null
            : getToolContinuationRenderState(displayMsg);
          const messageMetadataRows =
            isAssistantMessage
              ? buildMessageMetadataRows(displayMsg, {
                  sourceLabel: messageSourceLabel,
                  toolCount: resolvedTools.length,
                  ragCount: ragMatches.length,
                })
              : [];
          const messageVisionNotice =
            isAssistantMessage && !isRegeneratingMessage
              ? getMessageVisionNotice(displayMsg)
              : null;
          const messageId = msg?.id || msg?.message_id || null;
          const subchatLinks =
            messageId &&
            subchatLinksByMessage &&
            Array.isArray(subchatLinksByMessage[messageId])
              ? subchatLinksByMessage[messageId]
              : [];
          return (
            <React.Fragment key={fragmentKey}>
              <div
              ref={(el) => {
                if (msg && msg.id) {
                  if (el) {
                    messageRefs.current[msg.id] = el;
                  } else {
                    delete messageRefs.current[msg.id];
                  }
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
              <RagContextPanel
                matches={ragMatches}
                defaultOpen={false}
                className="message-rag-context"
                onToggle={(open) => {
                  if (!open || !msg?.id) return;
                  if (typeof requestAnimationFrame === "function") {
                    requestAnimationFrame(() =>
                      scrollMessageIntoView(msg.id, "smooth", { block: "start" }),
                    );
                  } else {
                    setTimeout(
                      () => scrollMessageIntoView(msg.id, "smooth", { block: "start" }),
                      0,
                    );
                  }
                }}
              />
              {toolContinuationRenderState
                ? toolContinuationRenderState.prelude
                  ? renderContent(
                      { ...displayMsg, text: toolContinuationRenderState.prelude },
                      idx,
                      resolvedTools,
                    )
                  : null
                : isRegeneratingMessage
                  ? null
                  : renderContent(displayMsg, idx, resolvedTools)}
              {messageVisionNotice && (
                <div
                  className={`message-vision-notice message-vision-notice--${messageVisionNotice.tone}`}
                  role="status"
                  aria-label="Image delivery notice"
                >
                  <strong>{messageVisionNotice.title}</strong>
                  <span>{messageVisionNotice.message}</span>
                </div>
              )}
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
                        source_url: att.source_url || att.sourceUrl || null,
                        source_url_recorded_at:
                          att.source_url_recorded_at || att.sourceUrlRecordedAt || null,
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
              {subchatLinks.length > 0 && (
                <div className="inline-subchat-list" aria-label="Subchats">
                  <div className="inline-subchat-toolbar">
                    <span className="inline-subchat-label">
                      subchats ({subchatLinks.length})
                    </span>
                  </div>
                  <div className="inline-subchat-items">
                    {subchatLinks.map((link, linkIndex) => {
                      const label = link?.label || link?.conversationId || "subchat";
                      const count =
                        typeof link?.messageCount === "number" && link.messageCount > 0
                          ? `${link.messageCount} messages`
                          : "";
                      const kind =
                        typeof link?.kind === "string" && link.kind.trim()
                          ? link.kind.trim()
                          : "subchat";
                      return (
                        <button
                          key={`${link?.conversationId || label}-${linkIndex}`}
                          type="button"
                          className="inline-subchat-link"
                          title={count ? `${label} - ${count}` : label}
                          aria-label={`Open subchat ${label}`}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            onOpenConversation?.(link?.conversationId, label);
                          }}
                        >
                          <span className="inline-subchat-kind">{kind}</span>
                          <span className="inline-subchat-title">{label}</span>
                          {count ? (
                            <span className="inline-subchat-count">{count}</span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
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
                const hasActionableTools = resolvedTools.some(isActionableToolStatus);
                const defaultToolsCollapsed = toolContinuationRenderState
                  ? !hasActionableTools
                  : collapseAllTools && !hasActionableTools;
                const toolsCollapsed = Object.prototype.hasOwnProperty.call(
                  collapsedTools,
                  toolCollapseKey,
                )
                  ? collapsedTools[toolCollapseKey]
                  : defaultToolsCollapsed;
                const toolCount = resolvedTools.length;
                return (
                  <div
                    className={`inline-tool-list${isActiveMessage ? " active" : ""}${
                      hasActionableTools ? " has-actionable-tools" : ""
                    }${toolContinuationRenderState ? " is-phase-batch" : ""}`}
                  >
                    <div className="inline-tool-toolbar">
                      <button
                        type="button"
                        className="tool-collapse-btn"
                        onClick={() => toggleToolCollapse(toolCollapseKey)}
                      >
                        {toolsCollapsed
                          ? `${
                              toolContinuationRenderState ? "show all tools" : "show tools"
                            } (${toolCount})`
                          : toolContinuationRenderState
                            ? "hide all tools"
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
                    const syntheticToolKey =
                      typeof tool?.synthetic_id === "string" ? tool.synthetic_id : "";
                    const toolDetailKey = `${toolCollapseKey}:${
                      requestId || syntheticToolKey || toolName || "tool"
                    }:${i}`;
                    const toolCardExpanded = expandedToolCards[toolDetailKey] === true;
                    const isRoutineResolvedTool = status === "invoked";
                    const showToolExecutionDetails =
                      isActiveMessage || toolCardExpanded || !isRoutineResolvedTool;
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
                      : hasArgs && showToolExecutionDetails
                        ? summarizeToolValue(tool.args, toolName)
                        : "";
                    const acceptDisabled = tool?.manual_fill_required === true && !requestId;
                    const localDenyAllowed = tool?.synthetic === true && !requestId;
                    const toolInspectorRows = [
                      {
                        label: "Source",
                        value: toolSourceLabel || "assistant tool proposal",
                      },
                      { label: "Status", value: statusLabel },
                      {
                        label: "Owner",
                        value: sessionIdForTool ? `session ${sessionIdForTool}` : "current chat",
                      },
                      {
                        label: "Request",
                        value: requestId || syntheticToolKey || "local fallback",
                      },
                      {
                        label: "Evidence",
                        value: requestId
                          ? "backend tool request"
                          : syntheticToolKey
                            ? "saved conversation tool state"
                            : "inline fallback state",
                      },
                      {
                        label: "Next",
                        value: isPending
                          ? acceptDisabled
                            ? "Edit required arguments before approval."
                            : "Approve, edit, or deny this tool."
                          : hasResult
                            ? "Open the card to inspect the tool result."
                            : "Open the card to inspect arguments.",
                      },
                    ];
                    const baselineToolSignature = toolSignature(tool);
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
                  const canRetryResolvedTool =
                    !isPending &&
                    Boolean(toolName && chainTarget) &&
                    ["error", "failed", "denied", "timeout", "cancelled", "canceled"].includes(status);
                  const retryResolvedTool = async (
                    overrideArgs,
                    overrideName,
                    continueTarget,
                  ) => {
                    const outcome = await invokeDirect(overrideArgs, overrideName);
                    if (!outcome || typeof outcome.result === "undefined") return;
                    const resolvedName = (overrideName || toolName || "").trim() || toolName;
                    const resolvedArgs = overrideArgs ?? baselineArgs ?? {};
                    const toolWithResult = {
                      ...(tool || {}),
                      id: requestId || tool?.id,
                      name: resolvedName,
                      args: resolvedArgs,
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
                  };
                  const resolveRoutingContinueTarget = (candidateArgs = baselineArgs) => {
                    if (toolName !== "route_to_local_model") return null;
                    const routeArgs =
                      candidateArgs && typeof candidateArgs === "object" && !Array.isArray(candidateArgs)
                        ? candidateArgs
                        : {};
                    const requestedMode = String(routeArgs.target_mode || "local")
                      .trim()
                      .toLowerCase();
                    const targetMode = ["local", "server", "api"].includes(requestedMode)
                      ? requestedMode
                      : "local";
                    const resolved = resolveModeModel(targetMode, state);
                    const routeModel = String(routeArgs.target_model || "").trim();
                    return {
                      mode: targetMode,
                      model: routeModel || resolved.model || "",
                      workflow: workflowProfile,
                    };
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
                            continueTarget || resolveRoutingContinueTarget(effectiveArgs),
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
                            continueTarget ||
                              resolveRoutingContinueTarget(overrideArgs ?? baselineArgs),
                          );
                        }
                      } else if (decision === "deny" && localDenyAllowed) {
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
                          const tIdx = existingTools.findIndex((entryTool) => {
                            if (!entryTool || typeof entryTool !== "object") return false;
                            if (
                              syntheticToolKey &&
                              entryTool.synthetic_id === syntheticToolKey
                            ) {
                              return true;
                            }
                            return toolSignature(entryTool) === baselineToolSignature;
                          });
                          if (tIdx === -1) return prev;
                          existingTools[tIdx] = {
                            ...existingTools[tIdx],
                            status: "denied",
                            result: buildToolOutcomeResult("denied", "Dismissed by user."),
                          };
                          msgEntry.tools = existingTools;
                          updated[mIdx] = msgEntry;
                          return { ...prev, conversation: updated };
                        });
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
                        }${
                          showToolExecutionDetails ? "" : " summary-only"
                        }`}
                        data-tool-id={requestId || undefined}
                        data-chain-id={chainTarget || undefined}
                        onToggle={(event) => {
                          const isOpen = event.currentTarget.open;
                          setExpandedToolCards((prev) => {
                            if (Boolean(prev[toolDetailKey]) === isOpen) return prev;
                            if (isOpen) {
                              return { ...prev, [toolDetailKey]: true };
                            }
                            const next = { ...prev };
                            delete next[toolDetailKey];
                            return next;
                          });
                          if (!isOpen || !chainTarget) return;
                          if (typeof requestAnimationFrame === "function") {
                            requestAnimationFrame(() =>
                              scrollMessageIntoView(chainTarget, "smooth", {
                                block: "start",
                              }),
                            );
                          } else {
                            setTimeout(
                              () =>
                                scrollMessageIntoView(chainTarget, "smooth", {
                                  block: "start",
                                }),
                              0,
                            );
                          }
                        }}
                      >
                      <summary className="tool-summary compact">
                        <div className="tool-summary-main">
                          <div className="tool-meta">
                            <span className="tool-step-index">{i + 1}</span>
                            <span className="tool-name">{tool.name || "tool"}</span>
                            {showToolExecutionDetails && (
                              <span className={`tool-status-badge status-${statusTone}`}>
                                <span className="tool-status-glyph" aria-hidden="true">
                                  {statusGlyph}
                                </span>
                                {statusLabel}
                              </span>
                            )}
                            {showToolExecutionDetails && (
                              <StateInspector
                                title="Why this tool is here"
                                summary={
                                  isPending
                                    ? "The assistant proposed this tool and is waiting for review."
                                    : "This tool state came from the current chat turn."
                                }
                                rows={toolInspectorRows}
                                ariaLabel={`Explain ${tool.name || "tool"} state`}
                              />
                            )}
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
                              disabled={acceptDisabled}
                              title={
                                acceptDisabled
                                  ? "This tool needs editable arguments before it can run."
                                  : `Approve and run ${tool.name || "this tool"}`
                              }
                              onClick={async (event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (acceptDisabled) return;
                                await submitDecision("accept");
                              }}
                            >
                              Accept
                            </button>
                            <button
                              type="button"
                              className="tool-action-btn deny"
                              disabled={!requestId && !localDenyAllowed}
                              title={
                                !requestId && !localDenyAllowed
                                  ? "This local fallback tool cannot be denied from the backend."
                                  : `Deny ${tool.name || "this tool"}`
                              }
                              onClick={async (event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (!requestId && !localDenyAllowed) return;
                                await submitDecision("deny");
                              }}
                            >
                              Deny
                            </button>
                            <button
                              type="button"
                              className="tool-action-btn edit"
                              title={`Edit ${tool.name || "tool"} arguments before running`}
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
                                    session_id: sessionIdForTool || state.sessionId || undefined,
                                    message_id: chainTarget || undefined,
                                  },
                                  onSchedule: async ({ args, name, schedule }) => {
                                    if (!schedule || !schedule.event_id) {
                                      throw new Error("Missing schedule details.");
                                    }
                                    const eventId = String(schedule.event_id);
                                    const resolvedName =
                                      (name || toolName || "").trim() || toolName || "tool";
                                    const reqId = requestId ? String(requestId) : eventId;
                                    const ownerSession =
                                      sessionIdForTool || state.sessionId || undefined;
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
                                          rrule: schedule.rrule,
                                          timezone: schedule.timezone,
                                          status: schedule.status || "scheduled",
                                          background_job: schedule.background_job,
                                          actions: [
                                            {
                                              id: reqId,
                                              request_id: reqId,
                                              kind: "tool",
                                              name: resolvedName,
                                              args: args || {},
                                              prompt: schedule.prompt,
                                              conversation_mode: schedule.conversation_mode,
                                              session_id: ownerSession,
                                              message_id: chainTarget || undefined,
                                              chain_id: chainTarget || undefined,
                                            },
                                          ],
                                        },
                                      );
                                      await axios.post("/api/tools/schedule", {
                                        request_id: reqId,
                                        event_id: eventId,
                                        name: resolvedName,
                                        args: args || {},
                                        prompt: schedule.prompt,
                                        conversation_mode: schedule.conversation_mode,
                                        session_id: ownerSession,
                                        message_id: chainTarget || undefined,
                                        chain_id: chainTarget || undefined,
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
                        {!isPending && canRetryResolvedTool && (
                          <div className="tool-actions inline resolved">
                            <button
                              type="button"
                              className="tool-action-btn retry"
                              title={`Retry ${tool.name || "this tool"} with the same arguments`}
                              onClick={async (event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                await retryResolvedTool();
                              }}
                            >
                              Retry
                            </button>
                            <button
                              type="button"
                              className="tool-action-btn edit"
                              title={`Edit ${tool.name || "tool"} arguments and retry`}
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
                                    await retryResolvedTool(args, name, continueTarget);
                                  },
                                  schedulePrefill: {
                                    start_time: Math.floor(Date.now() / 1000),
                                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                                    title: `Retry tool: ${toolName || "tool"}`,
                                    session_id: sessionIdForTool || state.sessionId || undefined,
                                    message_id: chainTarget || undefined,
                                  },
                                });
                              }}
                            >
                              Edit & retry
                            </button>
                          </div>
                        )}
                        <span className="tool-summary-caret" aria-hidden="true">
                          {">"}
                        </span>
                      </summary>
                      {showToolExecutionDetails &&
                        (hasArgs || hasResult || toolSourceLabel) && (
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
                            <div className="tool-result-inline" aria-label="Tool result">
                              <ToolPayloadView
                                value={tool.result}
                                toolName={tool.name}
                                kind="result"
                                compact
                                onOpenComputerSession={openBrowserSessionInspector}
                              />
                            </div>
                          )}
                        </div>
                      )}
                    </details>
                    );
                  })}
                  </div>
                );
              })()}
              {toolContinuationRenderState &&
                renderToolContinuationPhases(
                  msg,
                  idx,
                  resolvedTools,
                  toolContinuationRenderState,
                )}
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
                    const needsToolContinue =
                      !unresolvedLoop &&
                      !!(
                        msg.metadata &&
                        typeof msg.metadata === "object" &&
                        msg.metadata.tool_response_pending
                      ) &&
                      Boolean(buildToolContinuationBatch(resolveMessageTools(msg)));
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
                    const ttsRoute = isTtsActive && ttsPlayback.route
                      ? ttsPlayback.route
                      : configuredTtsRoute;
                    const ttsRouteTooltip = ttsRoute?.tooltip || "Text-to-speech route: server default";
                    const ttsActionTooltip =
                      isTtsActive && ttsPlayback.status !== "loading"
                        ? `Pause/resume speech. ${ttsRouteTooltip}`
                        : `Speak this response. ${ttsRouteTooltip}`;
                    return (
                    <div className="message-actions">
                      {messageMetadataRows.length > 0 && (
                        <StateInspector
                          title="Message metadata"
                          summary="Routing, retrieval, and tool details for this response."
                          rows={messageMetadataRows}
                          label="i"
                          placement="top"
                          className="message-state-inspector"
                          ariaLabel="Show message metadata"
                        />
                      )}
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
                          <span>
                            <button
                              type="button"
                              className={`chip msg-action-chip${unresolvedLoop ? " retry" : ""}${
                                needsToolContinue ? " needs-tool-continue" : ""
                              }`}
                              onClick={() => continueGenerating(msg)}
                              disabled={loading}
                              aria-label="Continue generating"
                            >
                              {unresolvedLoop ? "retry continue" : "continue"}
                            </button>
                          </span>
                        </Tooltip>
                      )}
                      <Tooltip
                        title={ttsActionTooltip}
                      >
                        <span>
                          <IconButton
                            className="regen-btn"
                            aria-label="Speak assistant response"
                            title={ttsActionTooltip}
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
                        </span>
                      </Tooltip>
                      {isTtsActive && (
                        <div
                          className="tts-progress"
                          title={ttsRouteTooltip}
                          aria-label={ttsRouteTooltip}
                        >
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
                        <span>
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
                        </span>
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
            {visibleIdx < visibleConversationMessages.length - 1 && (
              <Divider className="chat-divider" />
            )}
            </React.Fragment>
        );
        })}
        <div ref={bottomSentinelRef} className="chat-bottom-sentinel" />
      </div>
      <div
        className={`chat-window-resizer${isChatWindowResizing ? " is-resizing" : ""}`}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize chat window"
        title="Drag to resize the chat window. Shift + Arrow keys resize faster. Home resets width."
        onPointerDown={startChatWindowResize}
        onDoubleClick={resetChatWindowWidth}
        onKeyDown={handleChatWindowResizeKeyDown}
        tabIndex={0}
      />
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
    {commandSuggestionsPopover}
    {inputAlertsPopover}
    {typeof document !== 'undefined' && createPortal(
      (entryOpen ? (
        <div
          className={`input-box${cameraOpen ? " camera-open" : ""}`}
          ref={inputBoxRef}
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
          {audioRecorderStatus && (
            <div className="input-status-strip" role="status" aria-live="polite">
              <span className="input-status-dot" aria-hidden="true" />
              <span>{audioRecorderStatus}</span>
            </div>
          )}
          {banner && (
            <div className="alert input-banner" role="status">
              <div className="input-banner-copy">
                <strong>{banner.message}</strong>
                {banner.hint && <span>{banner.hint}</span>}
              </div>
              <div className="input-banner-actions">
                {banner.actions &&
                  banner.actions.map((a, i) => (
                    <button key={i} className="chip" onClick={a.onClick}>
                      {a.label}
                    </button>
                  ))}
                <button className="chip" onClick={() => setBanner(null)}>
                  dismiss
                </button>
              </div>
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
              <div
                className="camera-capture-stage"
                role="region"
                aria-label="Camera preview"
              >
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
                  {attachments.map((att) => {
                    const attachmentUrl = att.remoteUrl || att.url;
                    const attachmentName = att.file?.name || att.name || "attachment";
                    const showImagePreview =
                      attachmentLooksImage(att) && Boolean(attachmentUrl);
                    return (
                      <div
                        key={att.id}
                        className={`attachment-chip${att.uploadFailed ? " attachment-chip--failed" : ""}`}
                        title={att.uploadError || attachmentName}
                      >
                        <button
                          type="button"
                          className="chip-preview"
                          aria-label={`Open ${attachmentName}`}
                          disabled={!attachmentUrl}
                          onClick={() =>
                            window.open(attachmentUrl, "_blank", "noopener")
                          }
                        >
                          {showImagePreview ? (
                            <img src={attachmentUrl} alt="" className="chip-thumb" />
                          ) : (
                            <span className="chip-icon" aria-hidden>
                              <AttachFileIcon fontSize="inherit" />
                            </span>
                          )}
                          <span className="chip-name">
                            {truncateFilename(attachmentName)}
                          </span>
                        </button>
                        {att.uploading && (
                          <span className="chip-uploading" aria-live="polite">
                            uploading{"\u2026"}
                          </span>
                        )}
                        {att.uploadFailed && (
                          <>
                            <span className="chip-upload-failed" aria-live="polite">
                              failed
                            </span>
                            <button
                              type="button"
                              className="chip-retry"
                              aria-label={`Retry ${attachmentName}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                retryAttachmentUpload(att);
                              }}
                            >
                              retry
                            </button>
                          </>
                        )}
                        <button
                          type="button"
                          className="chip-remove"
                          aria-label={`Remove ${attachmentName}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            removeAttachment(att.id);
                          }}
                        >
                          <CloseIcon fontSize="small" />
                        </button>
                      </div>
                    );
                  })}
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
            <div className="input-main" ref={inputMainRef}>
              <TextField
                value={message}
                onChange={handleComposerChange}
                onKeyDown={handleKeyDown}
                onPaste={handleComposerPaste}
                disabled={loading && !isStreaming}
                placeholder="Type your message..."
                size="medium"
                multiline
                inputRef={composerInputRef}
                inputProps={{
                  className: "composer-rich-input",
                  onSelect: handleComposerSelectionChange,
                  onClick: handleComposerSelectionChange,
                  onKeyUp: handleComposerSelectionChange,
                }}
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
                      aria-expanded={chatSettingsOpen}
                      aria-controls={chatSettingsOpen ? "chat-settings-popover" : undefined}
                      aria-haspopup="dialog"
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
                      : audioTranscribing
                        ? "Transcribing audio..."
                        : liveStreamingActive
                          ? "Live streaming mode is using the microphone"
                          : "Record audio message"
                  }
                >
                  <span>
                    <IconButton
                      onClick={toggleAudioRecording}
                      color={audioRecording ? "error" : "default"}
                      aria-label="record audio message"
                      className={`action-icon audio-record-toggle${
                        audioRecording ? " is-recording" : ""
                      }${audioTranscribing ? " is-transcribing" : ""}${
                        liveStreamingActive ? " is-live-disabled" : ""
                      }`}
                      disabled={audioTranscribing || liveStreamingActive}
                    >
                      <MicIcon />
                    </IconButton>
                  </span>
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
                    }${recording || liveSessionPending ? " is-recording" : ""}${
                      speaking ? " is-speaking" : ""
                    }`}
                  >
                    <FiberManualRecordIcon />
                  </IconButton>
                </Tooltip>
                <div className="attach-menu" ref={attachmentMenuRef}>
                  {attachmentMenuOpen &&
                    typeof document !== "undefined" &&
                    createPortal(
                      <div
                        ref={attachmentPopoverRef}
                        id="chat-attachment-menu"
                        className="attach-popover attach-popover-floating"
                        role="toolbar"
                        aria-label="Attachment actions"
                        style={attachmentPopoverStyle || attachmentPopoverFallbackStyle}
                      >
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
                      </div>,
                      document.body,
                    )}
                  <Tooltip title="Attachments">
                    <IconButton
                      ref={attachmentTriggerRef}
                      onClick={() => setAttachmentMenuOpen((prev) => !prev)}
                      aria-label="open attachments"
                      aria-haspopup="true"
                      aria-expanded={attachmentMenuOpen}
                      aria-controls={attachmentMenuOpen ? "chat-attachment-menu" : undefined}
                      className={`action-icon attach-trigger${
                        attachmentMenuOpen ? " is-open" : ""
                      }`}
                    >
                      <AttachFileIcon />
                    </IconButton>
                  </Tooltip>
                </div>
                <div className="send-stack">
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
