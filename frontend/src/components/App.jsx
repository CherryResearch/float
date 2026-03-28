import React, { useState, useContext, useEffect, useMemo, useRef, useCallback } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  useLocation,
  Link,
} from "react-router-dom";
import Chat from "./Chat";
import HistorySidebar from "./HistorySidebar";
import AgentConsole from "./AgentConsole";
import Settings from "./Settings";
import Visualization from "./Visualization";
import KnowledgeViewer from "./KnowledgeViewer";
import "../styles/App.css";
import { GlobalContext } from "../main";
import DevPanel from "./DevPanel";
import TopBar from "./TopBar";
import DownloadTray from "./DownloadTray";
import Notifications from "./Notifications";
import ErrorBoundary from "./ErrorBoundary";
import NotFound from "./NotFound";
import axios from "axios";
import { buildToolContinuationSignature } from "../utils/toolContinuations";
import {
  handleUnifiedPress,
  supportsHoverInteractions,
} from "../utils/pointerInteractions";

const MAX_AGENT_EVENTS = 20;
const EMPTY_GLOBAL_STATE = Object.freeze({});
const NOOP_SET_STATE = () => {};
const CONTINUATION_PLACEHOLDER_PATTERNS = [
  /^Requested\s+tools?\b/i,
  /^Tool results:/i,
  /^Tool results are available\./i,
  /^I couldn't finish the continuation from tool results\./i,
];

const isContinuationPlaceholderText = (value) => {
  const trimmed = String(value || "").trim();
  if (!trimmed) return false;
  return CONTINUATION_PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(trimmed));
};

const looksLikeUuid = (value) =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

const looksLikeSessionId = (value) =>
  typeof value === "string" && /^sess-\d+$/.test(value);

const humanizeAgentLabel = (raw, agentId) => {
  const explicit = raw.agent_label || raw.agent_name || raw.name || raw.title;
  if (explicit) return explicit;
  const messageId = raw.message_id || raw.chain_id;
  if (messageId) return `chat ${String(messageId).slice(-6)}`;
  if (looksLikeSessionId(agentId)) return `session ${String(agentId).slice(-6)}`;
  if (looksLikeUuid(agentId)) return `agent ${String(agentId).slice(0, 8)}`;
  return agentId || "orchestrator";
};

const normalizeConsoleEvent = (raw, fallbackAgent) => {
  if (!raw || typeof raw !== "object") return null;
  if (raw.type === "keepalive") return null;
  let normalizedType = raw.type;
  let normalizedContent = raw.content;
  let normalizedStatus = raw.status;
  let streamFragment = null;
  let streamArguments = null;
  let streamName = null;
  if (raw.type === "tool_call_delta") {
    normalizedType = "stream";
    streamName = typeof raw.name === "string" ? raw.name.trim() : "";
    streamFragment = typeof raw.fragment === "string" ? raw.fragment : null;
    streamArguments = typeof raw.arguments === "string" ? raw.arguments : null;
    normalizedStatus = raw.status || "streaming";
    normalizedContent = streamName ? `tool call: ${streamName}` : "tool call streaming";
  } else if (raw.type === "stream_status") {
    normalizedType = "stream";
    normalizedStatus = raw.status || "streaming";
    normalizedContent = raw.status ? `stream ${raw.status}` : "stream update";
  } else if (raw.type === "thought" && typeof raw.content === "string") {
    normalizedContent = collapseTokenizedLines(stripHarmonyEnvelope(raw.content));
  }
  const timestamp =
    typeof raw.timestamp === "number" && Number.isFinite(raw.timestamp)
      ? raw.timestamp
      : Date.now() / 1000;
  const agentId =
    raw.agent_id ||
    raw.chain_id ||
    raw.session_id ||
    raw.message_id ||
    fallbackAgent ||
    "orchestrator";
  const agentLabel = humanizeAgentLabel(raw, agentId);
  const agentStatus = raw.agent_status || raw.status || "active";
  return {
    ...raw,
    type: normalizedType,
    ...(typeof normalizedContent !== "undefined" ? { content: normalizedContent } : {}),
    ...(typeof normalizedStatus !== "undefined" ? { status: normalizedStatus } : {}),
    ...(streamName ? { stream_name: streamName } : {}),
    ...(streamFragment ? { stream_fragment: streamFragment } : {}),
    ...(streamArguments ? { stream_arguments: streamArguments } : {}),
    agent_id: agentId,
    agent_label: agentLabel,
    agent_status: agentStatus,
    timestamp,
  };
};

const stableJsonStringify = (value) => {
  const seen = new WeakSet();
  const helper = (v) => {
    if (v === null) return "null";
    const t = typeof v;
    if (t === "number" || t === "boolean" || t === "string") return JSON.stringify(v);
    if (t !== "object") return "null";
    if (seen.has(v)) return "\"[Circular]\"";
    seen.add(v);
    if (Array.isArray(v)) {
      return `[${v.map((item) => (typeof item === "undefined" ? "null" : helper(item))).join(",")}]`;
    }
    const keys = Object.keys(v).sort();
    const entries = [];
    keys.forEach((key) => {
      const val = v[key];
      if (typeof val === "undefined") return;
      entries.push(`${JSON.stringify(key)}:${helper(val)}`);
    });
    return `{${entries.join(",")}}`;
  };
  try {
    return helper(value);
  } catch {
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
};

const normalizeStreamFragment = (value) => {
  if (typeof value !== "string") return "";
  return value.replace(/\s+/g, " ").trim();
};

const truncateStreamFragment = (value, maxLength = 160) => {
  const text = normalizeStreamFragment(value);
  if (!text) return "";
  if (text.length <= maxLength) return text;
  const clipped = text.slice(0, Math.max(0, maxLength - 3)).trimEnd();
  return `${clipped}...`;
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

const toolSignature = (event) => {
  if (!event || event.type !== "tool") return null;
  const toolId = event.id ?? event.request_id ?? null;
  if (toolId !== null && typeof toolId !== "undefined" && String(toolId).trim()) {
    return `id:${String(toolId)}`;
  }
  const chain = event.chain_id || event.message_id || "global";
  const name = event.name || "tool";
  const argsSig = stableJsonStringify(event.args || {});
  return `sig:${chain}:${name}:${argsSig}`;
};

const toolLooseSignature = (event) => {
  if (!event || event.type !== "tool") return null;
  const chain = event.chain_id || event.message_id || "global";
  const name = event.name || "tool";
  const argsSig = stableJsonStringify(event.args || {});
  return `sig:${chain}:${name}:${argsSig}`;
};

const mergeToolEvent = (existing, incoming) => {
  const merged = { ...existing, ...incoming };
  if (!merged.id && (incoming?.request_id || existing.request_id)) {
    merged.id = incoming?.request_id || existing.request_id;
  }
  if (!merged.request_id && (incoming?.id || existing.id)) {
    merged.request_id = incoming?.id || existing.id;
  }
  if (!incoming?.args || !Object.keys(incoming.args || {}).length) {
    merged.args = existing.args;
  }
  if (typeof incoming?.result === "undefined") {
    merged.result = existing.result;
  }
  if (!incoming?.status && existing.status) {
    merged.status = existing.status;
  }
  if (!incoming?.timestamp && existing.timestamp) {
    merged.timestamp = existing.timestamp;
  }
  return merged;
};

const appendAgentEvent = (events, event) => {
  const list = Array.isArray(events) ? [...events] : [];
  if (!event) return list;
  if (event.type === "content") {
    const last = list[list.length - 1];
    const sameMessage =
      last &&
      last.type === "content" &&
      ((last.message_id && last.message_id === event.message_id) ||
        (last.chain_id && last.chain_id === event.chain_id) ||
        (!last.message_id && !event.message_id && last.agent_id === event.agent_id));
    if (sameMessage) {
      const combined = `${last.content || ""}${event.content || ""}`;
      list[list.length - 1] = {
        ...last,
        content: combined,
        timestamp: event.timestamp || last.timestamp,
      };
      return list;
    }
    list.push(event);
    return list;
  }
  if (event.type === "thought") {
    const last = list[list.length - 1];
    const sameMessage =
      last &&
      last.type === "thought" &&
      ((last.message_id && last.message_id === event.message_id) ||
        (last.chain_id && last.chain_id === event.chain_id) ||
        (!last.message_id && !event.message_id && last.agent_id === event.agent_id));
    if (sameMessage) {
      const combined = collapseTokenizedLines(
        `${last.content || ""}${event.content || ""}`,
      );
      list[list.length - 1] = {
        ...last,
        content: combined,
        timestamp: event.timestamp || last.timestamp,
      };
      return list;
    }
    list.push(event);
    return list;
  }
  if (event.type === "stream") {
    const last = list[list.length - 1];
    const sameMessage =
      last &&
      last.type === "stream" &&
      ((last.message_id && last.message_id === event.message_id) ||
        (last.chain_id && last.chain_id === event.chain_id) ||
        (!last.message_id && !event.message_id && last.agent_id === event.agent_id));
    const name = typeof event.name === "string" ? event.name.trim() : "";
    const callIndex =
      typeof event.call_index === "number" && Number.isFinite(event.call_index)
        ? event.call_index
        : null;
    const fragment =
      event.stream_fragment ||
      event.fragment ||
      event.stream_arguments ||
      event.arguments ||
      event.content ||
      "";
    const preview = truncateStreamFragment(fragment);
    if (sameMessage) {
      const streamChunks = (last.stream_chunks || 0) + 1;
      const streamNames = Array.isArray(last.stream_names) ? [...last.stream_names] : [];
      if (name && !streamNames.includes(name)) {
        streamNames.push(name);
      }
      const streamCallIndices = Array.isArray(last.stream_call_indices)
        ? [...last.stream_call_indices]
        : [];
      if (callIndex !== null && !streamCallIndices.includes(callIndex)) {
        streamCallIndices.push(callIndex);
      }
      list[list.length - 1] = {
        ...last,
        ...event,
        status: event.status || last.status || "streaming",
        content: event.content || last.content,
        stream_chunks: streamChunks,
        stream_names: streamNames,
        stream_call_indices: streamCallIndices,
        stream_preview: preview || last.stream_preview || "",
        timestamp: event.timestamp || last.timestamp,
      };
      return list;
    }
    list.push({
      ...event,
      status: event.status || "streaming",
      stream_chunks: 1,
      stream_names: name ? [name] : [],
      stream_call_indices: callIndex !== null ? [callIndex] : [],
      stream_preview: preview || "",
    });
    return list;
  }
  if (event.type === "tool") {
    const signature = toolSignature(event);
    let idx =
      signature !== null ? list.findIndex((item) => toolSignature(item) === signature) : -1;
    if (idx === -1 && (event.id || event.request_id)) {
      const loose = toolLooseSignature(event);
      if (loose) {
        idx = list.findIndex((item) => {
          if (!item || item.type !== "tool") return false;
          if (item.id || item.request_id) return false;
          return toolLooseSignature(item) === loose;
        });
      }
    }
    if (idx >= 0) {
      list[idx] = mergeToolEvent(list[idx], event);
      return list;
    }
    list.push(event);
    return list;
  }
  list.push(event);
  return list;
};

const reduceAgentState = (prev, event) => {
  if (!event) return prev;
  const agentId = event.agent_id || "orchestrator";
  const nextOrder = [agentId, ...prev.order.filter((id) => id !== agentId)];
  const fallbackLabel =
    event.agent_label ||
    event.agent_name ||
    event.name ||
    event.title ||
    (event.message_id ? `chat ${String(event.message_id).slice(-6)}` : null) ||
    (event.chain_id ? `chat ${String(event.chain_id).slice(-6)}` : null) ||
    agentId;
  const existing = prev.byId[agentId] || {
    id: agentId,
    label: fallbackLabel,
    status: event.agent_status,
    summary: "",
    updatedAt: event.timestamp,
    events: [],
  };
  const events = appendAgentEvent(existing.events || [], event);
  if (events.length > MAX_AGENT_EVENTS) {
    events.splice(0, events.length - MAX_AGENT_EVENTS);
  }
  const summary =
    event.type === "thought" && typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : existing.summary;
  const updatedAt = event.timestamp || existing.updatedAt;
  const nextAgent = {
    ...existing,
    label: event.agent_label || event.agent_name || existing.label || fallbackLabel,
    status: event.agent_status || existing.status,
    summary,
    updatedAt,
    events,
  };
  return {
    byId: {
      ...prev.byId,
      [agentId]: nextAgent,
    },
    order: nextOrder,
  };
};

const buildAgentStateFromSnapshot = (agents, { includeContent = false } = {}) => {
  const byId = {};
  const order = [];
  (agents || []).forEach((agent) => {
    const id = agent.id || agent.agent_id || agent.session_id || agent.chain_id;
    if (!id) return;
    const normalizedEvents = Array.isArray(agent.events)
      ? agent.events
          .map((item) => normalizeConsoleEvent(item, id))
          .filter(
            (entry) => entry && (includeContent || entry.type !== "content"),
          )
      : [];
    let events = [];
    normalizedEvents.forEach((entry) => {
      events = appendAgentEvent(events, entry);
    });
    if (events.length > MAX_AGENT_EVENTS) {
      events = events.slice(-MAX_AGENT_EVENTS);
    }
    const lastEventTs =
      agent.updated_at || agent.updatedAt || agent.last_event_ts || events.at(-1)?.timestamp;
    byId[id] = {
      id,
      label: agent.label || agent.agent_label || agent.name || id,
      status: agent.status || agent.agent_status || "idle",
      summary: agent.summary || agent.last_thought || "",
      updatedAt: lastEventTs || Date.now() / 1000,
      resources: agent.resources || agent.resource || null,
      events,
    };
    order.push(id);
  });
  order.sort((a, b) => (byId[b].updatedAt || 0) - (byId[a].updatedAt || 0));
  return { byId, order };
};

const normalizeActionSummary = (raw) => {
  if (!raw || typeof raw !== "object") return null;
  const id = raw.id || raw.action_id;
  if (!id) return null;
  const createdAtTs =
    typeof raw.created_at_ts === "number" && Number.isFinite(raw.created_at_ts)
      ? raw.created_at_ts
      : typeof raw.timestamp === "number" && Number.isFinite(raw.timestamp)
        ? raw.timestamp
        : Date.now() / 1000;
  return {
    ...raw,
    id: String(id),
    created_at_ts: createdAtTs,
    timestamp: createdAtTs,
    item_count: Number(raw.item_count || 0),
    revertible: !!raw.revertible,
  };
};

const mergeActionSummary = (actions, rawAction) => {
  const normalized = normalizeActionSummary(rawAction);
  if (!normalized) return Array.isArray(actions) ? actions : [];
  const list = Array.isArray(actions) ? [...actions] : [];
  const idx = list.findIndex((item) => item && item.id === normalized.id);
  if (idx >= 0) {
    list[idx] = { ...list[idx], ...normalized };
  } else {
    list.push(normalized);
  }
  list.sort(
    (a, b) => (Number(b?.created_at_ts) || 0) - (Number(a?.created_at_ts) || 0),
  );
  return list.slice(0, 300);
};

const buildActionHistoryFromSnapshot = (actions) => {
  let list = [];
  (Array.isArray(actions) ? actions : []).forEach((action) => {
    list = mergeActionSummary(list, action);
  });
  return list;
};

const normalizeSyncReviewSummary = (raw) => {
  if (!raw || typeof raw !== "object") return null;
  const requestedSectionLabels = Array.isArray(raw.requested_section_labels)
    ? raw.requested_section_labels
        .map((value) => String(value || "").trim())
        .filter(Boolean)
    : [];
  const requestedSections = Array.isArray(raw.requested_sections)
    ? raw.requested_sections
        .map((value) => String(value || "").trim())
        .filter(Boolean)
    : [];
  return {
    id: String(raw.id || "").trim(),
    status: String(raw.status || "").trim() || "pending",
    created_at: Number(raw.created_at || 0),
    updated_at: Number(raw.updated_at || 0),
    source_label: String(raw.source_label || "").trim() || "remote device",
    device_name: String(raw.device_name || "").trim(),
    device_id: String(raw.device_id || "").trim(),
    requested_section_labels: requestedSectionLabels,
    requested_sections: requestedSections,
    decision: String(raw.decision || "").trim(),
    note: String(raw.note || "").trim(),
    effective_namespace: String(raw.effective_namespace || "").trim(),
  };
};

const buildSyncReviewsFromSnapshot = (payload) => {
  const raw = payload && typeof payload === "object" ? payload : {};
  return {
    pending: (Array.isArray(raw.pending) ? raw.pending : [])
      .map(normalizeSyncReviewSummary)
      .filter(Boolean),
    recent: (Array.isArray(raw.recent) ? raw.recent : [])
      .map(normalizeSyncReviewSummary)
      .filter(Boolean),
  };
};

const AppContent = () => {
  const globalContext = useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const location = useLocation();
  const [consoleEvents, setConsoleEvents] = useState([]);
  const [agentState, setAgentState] = useState({ byId: {}, order: [] });
  const [actionHistory, setActionHistory] = useState([]);
  const [syncReviews, setSyncReviews] = useState({ pending: [], recent: [] });
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [streamThoughts, setStreamThoughts] = useState(true);
  const [consoleFocus, setConsoleFocus] = useState(null);
  const isMobileLayout = useCallback(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 600 || window.innerHeight > window.innerWidth;
  }, []);
  const [leftOpen, setLeftOpen] = useState(!isMobileLayout());
  const [rightOpen, setRightOpen] = useState(!isMobileLayout());
  const [activeMessageId, setActiveMessageId] = useState(null);
  const stateRef = useRef(state);
  const activeMessageIdRef = useRef(activeMessageId);
  const leftHoverTimer = useRef(null);
  const rightHoverTimer = useRef(null);
  const focusClearTimer = useRef(null);
  const backendReady = state.backendMode === "api" ? state.apiStatus === "online" : true;
  const approvalLevelRef = useRef(state.approvalLevel);
  const apiModelRef = useRef(state.apiModel);
  const backendModeRef = useRef(state.backendMode);
  const localModelRef = useRef(state.localModel);
  const transformerModelRef = useRef(state.transformerModel);
  const thinkingModeRef = useRef(state.thinkingMode);
  const unloadLocalRef = useRef(false);
  const skipFirstBackendModeUnloadRef = useRef(true);
  const skipFirstLocalModelUnloadRef = useRef(true);
  const autoAcceptedToolIdsRef = useRef(new Set());
  const autoContinuedToolIdsRef = useRef(new Set());
  const autoAcceptedToolIdsByMessageRef = useRef(new Map());
  const autoResolvedToolsByMessageRef = useRef(new Map());
  const autoContinuingMessageIdsRef = useRef(new Set());

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const loadWidth = (storageKey, cssVar) => {
      try {
        const raw = localStorage.getItem(storageKey);
        const parsed = raw ? parseFloat(raw) : NaN;
        if (!Number.isFinite(parsed)) return;
        const maxWidth = Math.max(220, Math.min(520, window.innerWidth - 160));
        const next = clamp(parsed, 220, maxWidth);
        root.style.setProperty(cssVar, `${next}px`);
      } catch {}
    };
    loadWidth("sidebarWidthLeft", "--sidebar-width-left");
    loadWidth("sidebarWidthRight", "--sidebar-width-right");
  }, []);

  useEffect(() => {
    approvalLevelRef.current = state.approvalLevel;
  }, [state.approvalLevel]);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    activeMessageIdRef.current = activeMessageId;
  }, [activeMessageId]);

  useEffect(() => {
    apiModelRef.current = state.apiModel;
  }, [state.apiModel]);

  useEffect(() => {
    backendModeRef.current = state.backendMode;
  }, [state.backendMode]);

  useEffect(() => {
    localModelRef.current = state.localModel;
  }, [state.localModel]);

  useEffect(() => {
    transformerModelRef.current = state.transformerModel;
  }, [state.transformerModel]);

  useEffect(() => {
    thinkingModeRef.current = state.thinkingMode;
  }, [state.thinkingMode]);

  useEffect(() => {
    if (skipFirstBackendModeUnloadRef.current) {
      skipFirstBackendModeUnloadRef.current = false;
      return;
    }
    if (state.backendMode === "local") return;
    const unloadIfLoaded = async () => {
      if (unloadLocalRef.current) return;
      unloadLocalRef.current = true;
      try {
        const res = await axios.get("/api/llm/local-status", {
          params: { quick: true },
          timeout: 2500,
        });
        const runtime = res?.data?.runtime;
        if (runtime?.loaded) {
          await axios.post("/api/llm/unload-local");
        }
      } catch {
        // Ignore unload failures; model can be released manually.
      } finally {
        unloadLocalRef.current = false;
      }
    };
    unloadIfLoaded();
  }, [state.backendMode]);

  useEffect(() => {
    if (skipFirstLocalModelUnloadRef.current) {
      skipFirstLocalModelUnloadRef.current = false;
      return;
    }
    if (state.backendMode !== "local") return;
    const unloadIfLoaded = async () => {
      if (unloadLocalRef.current) return;
      unloadLocalRef.current = true;
      try {
        const res = await axios.get("/api/llm/local-status", {
          params: { quick: true },
          timeout: 2500,
        });
        const runtime = res?.data?.runtime;
        if (runtime?.loaded) {
          await axios.post("/api/llm/unload-local");
        }
      } catch {
        // Ignore unload failures; model can be released manually.
      } finally {
        unloadLocalRef.current = false;
      }
    };
    unloadIfLoaded();
  }, [state.backendMode, state.localModel]);

  useEffect(() => {
    autoAcceptedToolIdsRef.current = new Set();
    autoContinuedToolIdsRef.current = new Set();
    autoAcceptedToolIdsByMessageRef.current = new Map();
    autoResolvedToolsByMessageRef.current = new Map();
    autoContinuingMessageIdsRef.current = new Set();
  }, [state.sessionId]);

  const agentList = useMemo(
    () => agentState.order.map((id) => agentState.byId[id]).filter(Boolean),
    [agentState],
  );

  const pushAgentEvent = useCallback(
    (rawEvent) => {
      const event = normalizeConsoleEvent(rawEvent, state.sessionId);
      if (!event) return;
      setAgentState((prev) => reduceAgentState(prev, event));
      const chainTarget = event.chain_id || event.message_id;
      if (chainTarget) {
        setConsoleFocus((prev) => {
          const sameChain =
            prev &&
            prev.chainId === String(chainTarget) &&
            (!event.agent_id || prev.agentId === event.agent_id);
          if (sameChain) return prev;
          return {
            chainId: String(chainTarget),
            agentId: event.agent_id || event.chain_id || event.session_id || null,
            ts: Date.now(),
          };
        });
      }
    },
    [state.sessionId],
  );

  const fetchAgentSnapshot = useCallback(async () => {
    if (!backendReady) return;
    setAgentsLoading(true);
    try {
      const res = await axios.get("/api/agents/console");
      const snapshot = buildAgentStateFromSnapshot(res.data?.agents || [], {
        includeContent: false,
      });
      setAgentState(snapshot);
      setActionHistory(buildActionHistoryFromSnapshot(res.data?.actions || []));
      setSyncReviews(buildSyncReviewsFromSnapshot(res.data?.sync_reviews));
    } catch (err) {
      console.error("Failed to load agent console snapshot", err);
    } finally {
      setAgentsLoading(false);
    }
  }, [backendReady]);

  const toggleLeft = () => {
    setLeftOpen((o) => {
      const next = !o;
      if (next && isMobileLayout()) setRightOpen(false);
      return next;
    });
  };

  const toggleRight = () => {
    setRightOpen((o) => {
      const next = !o;
      if (next && isMobileLayout()) setLeftOpen(false);
      return next;
    });
  };

  const openLeft = () => {
    setLeftOpen(true);
    if (isMobileLayout()) setRightOpen(false);
  };

  const openRight = () => {
    setRightOpen(true);
    if (isMobileLayout()) setLeftOpen(false);
  };

  const clearLeftHoverTimer = () => {
    if (leftHoverTimer.current) {
      clearTimeout(leftHoverTimer.current);
      leftHoverTimer.current = null;
    }
  };

  const clearRightHoverTimer = () => {
    if (rightHoverTimer.current) {
      clearTimeout(rightHoverTimer.current);
      rightHoverTimer.current = null;
    }
  };

  const focusConsoleOnTarget = useCallback(
    (target) => {
      if (!target || typeof target !== "object") return;
      const chainId =
        target.chainId || target.messageId || target.id || target.message_id || null;
      const toolId = target.toolId || target.tool_id || null;
      const agentId = target.agentId || target.agent_id || null;
      if (!chainId && !toolId && !agentId) return;
      const payload = {
        chainId,
        toolId,
        agentId,
        ts: Date.now(),
      };
      setConsoleFocus(payload);
      setRightOpen(true);
      if (isMobileLayout()) setLeftOpen(false);
      if (focusClearTimer.current) {
        clearTimeout(focusClearTimer.current);
        focusClearTimer.current = null;
      }
      if (typeof window !== "undefined" && typeof window.setTimeout === "function") {
        focusClearTimer.current = window.setTimeout(() => {
          setConsoleFocus((prev) => (prev && prev.ts === payload.ts ? null : prev));
          if (focusClearTimer.current) {
            clearTimeout(focusClearTimer.current);
            focusClearTimer.current = null;
          }
        }, 5000);
      }
    },
    [isMobileLayout],
  );

  useEffect(() => {
    if (!backendReady) return;
    fetchAgentSnapshot();
  }, [backendReady, fetchAgentSnapshot]);

  useEffect(() => {
    let cancelled = false;
    let ws = null;
    let timer = null;
    let attempts = 0;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/api/ws/thoughts`;

    const attachHandlers = (sock) => {
      sock.onopen = () => {
        if (cancelled) return;
        attempts = 0;
        setState((prev) => ({
          ...prev,
          wsStatus: "online",
          wsLastError: "",
          wsLastErrorAt: null,
        }));
      };
      sock.onmessage = (e) => {
        if (!cancelled) {
          const now = Date.now();
          setState((prev) =>
            prev.wsLastEventAt === now
              ? prev
              : { ...prev, wsLastEventAt: now },
          );
        }
        try {
          const payload = JSON.parse(e.data);
          const actionEvent =
            payload && payload.type === "action" ? normalizeActionSummary(payload) : null;
          const currentState = stateRef.current;
          const event = normalizeConsoleEvent(payload, currentState?.sessionId);
          if (!event) return;
          if (actionEvent) {
            setActionHistory((prev) => mergeActionSummary(prev, actionEvent));
          }
          setConsoleEvents((prev) => {
            if (event.type === "content" && !stateRef.current?.devMode) {
              return prev;
            }
            const next = [...prev];
            const isContent = event.type === "content";
            if (isContent && typeof event.content === "string" && event.content) {
              const last = next[next.length - 1];
              const sameStream =
                last &&
                last.type === "content" &&
                (last.message_id === event.message_id ||
                  last.chain_id === event.chain_id) &&
                last.agent_id === event.agent_id;
              if (sameStream) {
                const combined = `${last.content || ""}${event.content}`;
                next[next.length - 1] = {
                  ...last,
                  content: combined,
                  timestamp: event.timestamp || last.timestamp,
                };
                return next;
              }
            }
            next.push(event);
            if (next.length > 200) next.splice(0, next.length - 200);
            return next;
          });
          if (event.type !== "content" && event.type !== "action") {
            pushAgentEvent(event);
          }

          if (
            event.type === "content" &&
            typeof event.content === "string" &&
            event.content &&
            (event.message_id || event.chain_id)
          ) {
            const messageId = event.message_id || event.chain_id;
            const activeId = activeMessageIdRef.current;
            setState((prev) => {
              const conversation = Array.isArray(prev.conversation)
                ? [...prev.conversation]
                : [];
              const idx = conversation.findIndex(
                (msg) => msg && typeof msg === "object" && msg.id === messageId,
              );
              if (idx === -1) return prev;
              const existing = conversation[idx];
              if (!existing || existing.role !== "ai") return prev;
              if (activeId && messageId !== activeId) return prev;
              if (!activeId && typeof existing.text === "string" && existing.text.trim()) {
                return prev;
              }
              const joined = `${existing.text || ""}${event.content}`;
              conversation[idx] = { ...existing, text: joined };
              return { ...prev, conversation };
            });
          }
          if (
            event.type === "thought" &&
            typeof event.content === "string" &&
            event.content.length > 0 &&
            (event.message_id || event.chain_id)
          ) {
            const messageId = event.message_id || event.chain_id;
            setState((prev) => {
              const conversation = Array.isArray(prev.conversation)
                ? [...prev.conversation]
                : [];
              const idx = conversation.findIndex(
                (msg) => msg && typeof msg === "object" && msg.id === messageId,
              );
              if (idx === -1) return prev;
              const entry = { ...conversation[idx] };
              const nextThoughts = appendThoughtChunk(entry.thoughts, event.content);
              if (nextThoughts === entry.thoughts) return prev;
              entry.thoughts = nextThoughts;
              conversation[idx] = entry;
              return { ...prev, conversation };
            });
          }
          if (event.type === "tool" && (event.message_id || event.chain_id)) {
            const mid = event.message_id || event.chain_id;
            setState((prev) => {
              const conv = Array.isArray(prev.conversation) ? [...prev.conversation] : [];
              const idx = conv.findIndex((m) => m && typeof m === "object" && m.id === mid);
              if (idx === -1) return prev;
              const msg = { ...conv[idx] };
              const tools = Array.isArray(msg.tools) ? [...msg.tools] : [];
              const sig = JSON.stringify({ name: event.name, args: event.args || {} });
              let tIdx = -1;
              if (event.id) tIdx = tools.findIndex((t) => t && t.id === event.id);
              if (tIdx === -1)
                tIdx = tools.findIndex(
                  (t) => JSON.stringify({ name: t?.name, args: t?.args || {} }) === sig,
                );
              const entry = {
                id: event.id,
                name: event.name,
                args: event.args || {},
                result:
                  typeof event.result !== "undefined"
                    ? event.result
                    : tIdx >= 0
                      ? tools[tIdx].result
                      : undefined,
                status: event.status || (tIdx >= 0 ? tools[tIdx].status : undefined),
                timestamp: event.timestamp,
              };
              if (tIdx >= 0) tools[tIdx] = { ...tools[tIdx], ...entry };
              else tools.push(entry);
              msg.tools = tools;
              conv[idx] = msg;
              return { ...prev, conversation: conv };
            });
          }

          if (event.type === "tool" && approvalLevelRef.current === "auto") {
            const toolStatus = String(event.status || "").toLowerCase();
            const rawToolId = event.id ?? event.request_id ?? null;
            const toolId =
              rawToolId !== null && typeof rawToolId !== "undefined"
                ? String(rawToolId).trim()
                : "";
            const messageId = event.message_id || event.chain_id || null;
            const chainId = event.chain_id || event.message_id || messageId || null;
            const sessionId = event.session_id || stateRef.current?.sessionId || null;

            const rememberAcceptedTool = (msgId, id) => {
              if (!msgId || !id) return;
              const map = autoAcceptedToolIdsByMessageRef.current;
              const existing = map.get(msgId) || new Set();
              existing.add(id);
              map.set(msgId, existing);
            };

            const forgetAcceptedTool = (msgId, id) => {
              if (!msgId || !id) return;
              const map = autoAcceptedToolIdsByMessageRef.current;
              const existing = map.get(msgId);
              if (!existing) return;
              existing.delete(id);
              if (!existing.size) map.delete(msgId);
            };

            const rememberResolvedTool = (msgId, id, rawEvent) => {
              if (!msgId || !id) return;
              const map = autoResolvedToolsByMessageRef.current;
              const existing = map.get(msgId) || new Map();
              existing.set(id, {
                id,
                name: rawEvent.name,
                args: rawEvent.args || {},
                result: rawEvent.result,
                status: rawEvent.status || "invoked",
              });
              map.set(msgId, existing);
            };

            if (toolId && messageId && autoAcceptedToolIdsRef.current.has(toolId)) {
              rememberAcceptedTool(messageId, toolId);
            }

            if (toolId && toolStatus === "proposed") {
              if (!autoAcceptedToolIdsRef.current.has(toolId)) {
                autoAcceptedToolIdsRef.current.add(toolId);
                if (messageId) rememberAcceptedTool(messageId, toolId);
                axios
                  .post("/api/tools/decision", {
                    request_id: toolId,
                    decision: "accept",
                    name: (event.name || "").trim() || event.name,
                    args: event.args || {},
                    session_id: sessionId || undefined,
                    message_id: messageId || undefined,
                    chain_id: chainId || undefined,
                  })
                  .catch((err) => {
                    console.error("Auto-accept failed", err);
                    autoAcceptedToolIdsRef.current.delete(toolId);
                    if (messageId) forgetAcceptedTool(messageId, toolId);
                  });
              }
            }

            const hasExplicitResult =
              typeof event.result !== "undefined" && event.result !== null;
            const fallbackResult =
              !hasExplicitResult && toolStatus === "denied"
                ? {
                    status: "denied",
                    ok: false,
                    message: "Denied by user.",
                    data: null,
                  }
                : !hasExplicitResult && toolStatus === "error"
                ? {
                    status: "error",
                    ok: false,
                    message: "Tool error.",
                    data: null,
                  }
                : null;
            const toolHasResult = hasExplicitResult || fallbackResult !== null;
            const isResolved =
              toolStatus &&
              ["invoked", "ok", "success", "complete", "error", "denied"].includes(
                toolStatus,
              );
            if (toolId && messageId && toolHasResult && isResolved) {
              const resolvedEvent = hasExplicitResult
                ? event
                : { ...event, result: fallbackResult };
              rememberResolvedTool(messageId, toolId, resolvedEvent);
            }

            const computeAutoContinueBatch = () => {
              if (!messageId) return null;
              const acceptedSet = autoAcceptedToolIdsByMessageRef.current.get(messageId);
              const resolvedMap = autoResolvedToolsByMessageRef.current.get(messageId);
              if (!acceptedSet || !acceptedSet.size || !resolvedMap) return null;

              const pendingIds = [...acceptedSet].filter(
                (id) => !autoContinuedToolIdsRef.current.has(id),
              );
              if (!pendingIds.length) return null;

              const readyIds = pendingIds.filter((id) => {
                const toolEntry = resolvedMap.get(id);
                if (!toolEntry) return false;
                const resVal = toolEntry.result;
                return typeof resVal !== "undefined" && resVal !== null;
              });
              if (readyIds.length !== pendingIds.length) return null;
              const toolPayload = readyIds
                .map((id) => resolvedMap.get(id))
                .filter(Boolean);
              if (!toolPayload.length) return null;
              return { readyIds, toolPayload };
            };

            const startAutoContinueIfReady = () => {
              if (!messageId || !sessionId) return;
              if (autoContinuingMessageIdsRef.current.has(messageId)) return;
              const batch = computeAutoContinueBatch();
              if (!batch) return;

              const { readyIds, toolPayload } = batch;
              autoContinuingMessageIdsRef.current.add(messageId);
              readyIds.forEach((id) => autoContinuedToolIdsRef.current.add(id));
              const thinkingValue = thinkingModeRef.current || "auto";
              const thinkingPayload =
                thinkingValue === "auto" ? {} : { thinking: thinkingValue };
              const mode = (backendModeRef.current || "api").toLowerCase();
              const model =
                mode === "local"
                  ? localModelRef.current || transformerModelRef.current || apiModelRef.current
                  : mode === "server"
                    ? transformerModelRef.current || apiModelRef.current
                    : apiModelRef.current;
              const toolContinueSignature = buildToolContinuationSignature(toolPayload);

              axios
                .post("/api/chat/continue", {
                  session_id: sessionId,
                  message_id: messageId,
                  model,
                  mode,
                  tools: toolPayload,
                  ...thinkingPayload,
                })
                .then((res) => {
                  const aiContinuation = res.data?.message || "";
                  const continuationThought = res.data?.thought || "";
                  const md = res.data?.metadata || {};
                  const returnedTools = Array.isArray(res.data?.tools_used)
                    ? res.data.tools_used
                    : [];
                  setState((prev) => {
                    const updated = Array.isArray(prev.conversation)
                      ? [...prev.conversation]
                      : [];
                    const mIdx = updated.findIndex((m) => m && m.id === messageId);
                    if (mIdx !== -1) {
                      const existingText = updated[mIdx]?.text || "";
                      const existingTrimmed = String(existingText || "").trim();
                      const placeholder = isContinuationPlaceholderText(existingTrimmed);
                      const joined =
                        placeholder && aiContinuation
                          ? aiContinuation
                          : existingText && existingTrimmed
                            ? aiContinuation
                              ? `${existingText}\n\n${aiContinuation}`.trim()
                              : existingText
                            : aiContinuation;
                      const existingTools = Array.isArray(updated[mIdx]?.tools)
                        ? [...updated[mIdx].tools]
                        : [];
                      const mergedTools = [...existingTools];
                      returnedTools.forEach((tool) => {
                        if (!tool || typeof tool !== "object") return;
                        const rawId = tool.id || tool.request_id || null;
                        const id = rawId ? String(rawId) : null;
                        let idx = -1;
                        if (id) {
                          idx = mergedTools.findIndex(
                            (t) =>
                              t &&
                              typeof t === "object" &&
                              String(t.id || t.request_id || "") === id,
                          );
                        }
                        if (idx === -1) {
                          const sig = JSON.stringify({
                            name: tool.name,
                            args: tool.args || {},
                          });
                          idx = mergedTools.findIndex(
                            (t) =>
                              t &&
                              typeof t === "object" &&
                              JSON.stringify({
                                name: t?.name,
                                args: t?.args || {},
                              }) === sig,
                          );
                        }
                        if (idx >= 0) mergedTools[idx] = { ...mergedTools[idx], ...tool };
                        else mergedTools.push(tool);
                      });
                      const updatedEntry = {
                        ...updated[mIdx],
                        ...(joined ? { text: joined } : {}),
                        timestamp: new Date().toISOString(),
                        ...(mergedTools.length ? { tools: mergedTools } : {}),
                        metadata: {
                          ...(updated[mIdx]?.metadata || {}),
                          ...(md || {}),
                          tool_continued: true,
                          ...(toolContinueSignature && !md?.tool_continue_signature
                            ? { tool_continue_signature: toolContinueSignature }
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
                    if (aiContinuation) {
                      if (hist.length && hist[hist.length - 1].role === "ai") {
                        const last = hist[hist.length - 1].text || "";
                        const lastTrimmed = String(last || "").trim();
                        const placeholder = isContinuationPlaceholderText(lastTrimmed);
                        hist[hist.length - 1] = {
                          role: "ai",
                          text:
                            placeholder && aiContinuation
                              ? aiContinuation
                              : last && lastTrimmed
                                ? `${last}\n\n${aiContinuation}`.trim()
                                : aiContinuation,
                        };
                      } else {
                        hist.push({ role: "ai", text: aiContinuation });
                      }
                    }
                    try {
                      localStorage.setItem("history", JSON.stringify(hist));
                      const payload = JSON.stringify({
                        sessionId: prev.sessionId,
                        history: hist,
                      });
                      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
                        const blob = new Blob([payload], { type: "application/json" });
                        navigator.sendBeacon("/api/history", blob);
                      } else {
                        axios
                          .post("/api/history", {
                            sessionId: prev.sessionId,
                            history: hist,
                          })
                          .catch(() => {});
                      }
                    } catch {}
                    return { ...prev, conversation: updated, history: hist };
                  });

                  const accepted = autoAcceptedToolIdsByMessageRef.current.get(messageId);
                  const resolved = autoResolvedToolsByMessageRef.current.get(messageId);
                  readyIds.forEach((id) => {
                    accepted?.delete(id);
                    resolved?.delete(id);
                  });
                  if (accepted && !accepted.size) {
                    autoAcceptedToolIdsByMessageRef.current.delete(messageId);
                  }
                  if (resolved && !resolved.size) {
                    autoResolvedToolsByMessageRef.current.delete(messageId);
                  }
                })
                .catch((err) => {
                  console.error("Auto-continue failed", err);
                  readyIds.forEach((id) => autoContinuedToolIdsRef.current.delete(id));
                })
                .finally(() => {
                  autoContinuingMessageIdsRef.current.delete(messageId);
                  startAutoContinueIfReady();
                });
            };

            startAutoContinueIfReady();
          }
        } catch (err) {
          console.error("Bad WS data", err);
          if (!cancelled) {
            setState((prev) => ({
              ...prev,
              wsLastError: err instanceof Error ? err.message : String(err),
              wsLastErrorAt: Date.now(),
            }));
          }
        }
      };
      sock.onerror = (event) => {
        if (!cancelled) {
          const message =
            (event && event.message) ||
            (event && event.reason) ||
            "connection error";
          setState((prev) => ({
            ...prev,
            wsStatus: "offline",
            wsLastError: message,
            wsLastErrorAt: Date.now(),
          }));
        }
        try {
          sock.close();
        } catch {}
      };
      sock.onclose = (event) => {
        if (cancelled) return;
        const message = event?.reason || (event?.code ? `code ${event.code}` : "");
        setState((prev) => ({
          ...prev,
          wsStatus: "offline",
          ...(event?.wasClean
            ? {}
            : {
                wsLastError: message || "connection closed unexpectedly",
                wsLastErrorAt: Date.now(),
              }),
        }));
        const backoff = Math.min(1000 * Math.pow(2, attempts), 30000);
        attempts += 1;
        timer = setTimeout(connect, backoff);
      };
    };

    const connect = () => {
      if (!streamThoughts || cancelled) return;
      try {
        setState((prev) =>
          prev.wsStatus === "loading"
            ? prev
            : { ...prev, wsStatus: "loading" },
        );
        ws = new WebSocket(url);
        attachHandlers(ws);
      } catch (e) {
        console.error("Thoughts WS connect failed", e);
        if (!cancelled) {
          setState((prev) => ({
            ...prev,
            wsStatus: "offline",
            wsLastError: e instanceof Error ? e.message : String(e),
            wsLastErrorAt: Date.now(),
          }));
        }
        timer = setTimeout(connect, 2000);
      }
    };

    if (streamThoughts) connect(); else setState((prev) => ({ ...prev, wsStatus: "offline" }));

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      try { if (ws) ws.close(); } catch {}
    };
  }, [streamThoughts, setState, pushAgentEvent, state.sessionId]);

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 600 || window.innerHeight > window.innerWidth) {
        setLeftOpen(false);
        setRightOpen(false);
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    return () => {
      if (focusClearTimer.current) {
        clearTimeout(focusClearTimer.current);
        focusClearTimer.current = null;
      }
    };
  }, []);

  const isCalendarView = location.pathname.startsWith("/knowledge");

  const filteredCalendarEvents = useMemo(() => {
    if (!isCalendarView) return [];

    const resolveTimestamp = (value) => {
      if (!value) return null;
      if (value instanceof Date) {
        const ms = value.getTime();
        return Number.isNaN(ms) ? null : ms;
      }
      if (typeof value === "number" && Number.isFinite(value)) {
        // Backend returns seconds; convert to ms when it looks like epoch seconds
        return value > 1e12 ? value : value * 1000;
      }
      if (typeof value === "string") {
        const parsed = new Date(value);
        const ms = parsed.getTime();
        return Number.isNaN(ms) ? null : ms;
      }
      if (typeof value === "object") {
        if (value.dateTime) return resolveTimestamp(value.dateTime);
        if (value.date) return resolveTimestamp(`${value.date}T00:00:00`);
      }
      return null;
    };

    const anchor = new Date(state.selectedCalendarDate);
    if (!Number.isNaN(anchor.getTime())) {
      anchor.setHours(0, 0, 0, 0);
    }
    const anchorMs = anchor.getTime();

    const enhanced = (state.calendarEvents || []).map((event) => {
      const startMs =
        resolveTimestamp(event.sidebarStart) ??
        resolveTimestamp(event.startDate) ??
        resolveTimestamp(event.start_time) ??
        resolveTimestamp(event.start) ??
        resolveTimestamp(event.start?.dateTime) ??
        resolveTimestamp(event.start?.date);
      const endMs =
        resolveTimestamp(event.sidebarEnd) ??
        resolveTimestamp(event.endDate) ??
        resolveTimestamp(event.end_time) ??
        resolveTimestamp(event.end) ??
        resolveTimestamp(event.end?.dateTime) ??
        resolveTimestamp(event.end?.date);
      return {
        ...event,
        sidebarStart: startMs,
        sidebarEnd: endMs,
      };
    });

    const withStart = enhanced.filter((evt) => Number.isFinite(evt.sidebarStart));
    const withoutStart = enhanced.filter((evt) => !Number.isFinite(evt.sidebarStart));

    const sorted = withStart.sort((a, b) => a.sidebarStart - b.sidebarStart);
    const upcoming = sorted.filter((evt) => evt.sidebarStart >= anchorMs);
    const primary = (upcoming.length > 0 ? upcoming : sorted).slice(0, 8);
    const extras = withoutStart.slice(0, Math.max(0, 8 - primary.length));

    return [...primary, ...extras];
  }, [isCalendarView, state.calendarEvents, state.selectedCalendarDate]);

  useEffect(() => {
    if (!isMobileLayout()) return;
    const handleDocumentPress = (e) => {
      const left = document.querySelector(".sidebar.left-sidebar");
      const right = document.querySelector(".sidebar.right-sidebar");
      const leftBtn = document.querySelector(".show-sidebar-btn.left");
      const rightBtn = document.querySelector(".show-sidebar-btn.right");
      if (
        leftOpen &&
        left &&
        !left.contains(e.target) &&
        (!leftBtn || !leftBtn.contains(e.target))
      ) {
        setLeftOpen(false);
      }
      if (
        rightOpen &&
        right &&
        !right.contains(e.target) &&
        (!rightBtn || !rightBtn.contains(e.target))
      ) {
        setRightOpen(false);
      }
    };
    document.addEventListener("pointerdown", handleDocumentPress);
    document.addEventListener("click", handleDocumentPress);
    return () => {
      document.removeEventListener("pointerdown", handleDocumentPress);
      document.removeEventListener("click", handleDocumentPress);
    };
  }, [isMobileLayout, leftOpen, rightOpen]);

  return (
    <div className="app-container">
      <TopBar />
      <HistorySidebar onToggle={toggleLeft} collapsed={!leftOpen} />
      {!leftOpen && (
        <button
          className="show-sidebar-btn left"
          onClick={(event) =>
            handleUnifiedPress(event, () => {
              clearLeftHoverTimer();
              openLeft();
            })
          }
          onPointerDown={(event) =>
            handleUnifiedPress(event, () => {
              clearLeftHoverTimer();
              openLeft();
            })
          }
          onMouseEnter={() => {
            if (isMobileLayout() || !supportsHoverInteractions()) return;
            clearLeftHoverTimer();
            // Hover-to-open delay tuned to ~0.6s per request
            leftHoverTimer.current = setTimeout(() => openLeft(), 600);
          }}
          onMouseLeave={clearLeftHoverTimer}
          title="Show history sidebar"
        >
          {">"}
        </button>
      )}
      <div className="main-chat">
        <div className="center-rail">
          <ErrorBoundary fallback={<div><Link to="/">Back to chat</Link></div>}>
            <Routes>
              <Route
                path="/"
                element={
                  <Chat
                    thoughts={consoleEvents}
                    activeMessageId={activeMessageId}
                    setActiveMessageId={setActiveMessageId}
                    onOpenConsole={focusConsoleOnTarget}
                  />
                }
              />
              <Route path="/settings" element={<Settings />} />
              <Route path="/visualization" element={<Visualization />} />
              <Route path="/knowledge" element={<KnowledgeViewer />} />
              {state.devMode && <Route path="/dev" element={<DevPanel />} />}
              <Route path="*" element={<NotFound />} />
            </Routes>
          </ErrorBoundary>
        </div>
      </div>
      <AgentConsole
        focus={consoleFocus}
        collapsed={!rightOpen}
        onToggle={toggleRight}
        streamEnabled={streamThoughts}
        onStreamToggle={() => setStreamThoughts((s) => !s)}
        agents={agentList}
        onSelectMessage={setActiveMessageId}
        isCalendar={isCalendarView}
        events={filteredCalendarEvents}
        backendReady={backendReady}
        loadingSnapshot={agentsLoading}
        onRefreshCalendar={() => {
          if (!backendReady) return;
          axios
            .post("/api/calendar/reminders/flush")
            .catch(() => {})
            .then(() =>
              axios.get("/api/calendar/events", { params: { detailed: true } }),
            )
            .then((res) => {
              setState((prev) => ({
                ...prev,
                calendarEvents: (res.data?.events || []).map((event) => {
                  const startISO = event.start_time
                    ? new Date(event.start_time * 1000).toISOString()
                    : event.start?.dateTime;
                  const endISO = event.end_time
                    ? new Date(event.end_time * 1000).toISOString()
                    : event.end?.dateTime;
                  return {
                    ...event,
                    summary: event.title || event.summary || event.id,
                    start: startISO ? { dateTime: startISO } : undefined,
                    end: endISO ? { dateTime: endISO } : undefined,
                  };
                }),
              }));
            })
            .catch((err) => console.error("Failed to refresh calendar", err));
        }}
        onRefreshAgents={fetchAgentSnapshot}
        actions={actionHistory}
        syncReviews={syncReviews}
      />
      {!rightOpen && (
        <button
          className="show-sidebar-btn right"
          onClick={(event) =>
            handleUnifiedPress(event, () => {
              clearRightHoverTimer();
              openRight();
            })
          }
          onPointerDown={(event) =>
            handleUnifiedPress(event, () => {
              clearRightHoverTimer();
              openRight();
            })
          }
          onMouseEnter={() => {
            if (isMobileLayout() || !supportsHoverInteractions()) return;
            clearRightHoverTimer();
            // Hover-to-open delay tuned to ~0.6s per request
            rightHoverTimer.current = setTimeout(() => openRight(), 600);
          }}
          onMouseLeave={clearRightHoverTimer}
          title="Show agent console"
        >
        {"<"}
        </button>
      )}
      <DownloadTray />
      <Notifications />
    </div>
  );
};

const App = () => {
  return (
    <Router>
      <AppContent />
    </Router>
  );
};

export default App;

export {
  stableJsonStringify,
  toolSignature,
  toolLooseSignature,
  mergeToolEvent,
  appendAgentEvent,
  reduceAgentState,
};
