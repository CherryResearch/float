import axios from "axios";

const RAW_API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const API_BASE_URL = RAW_API_BASE.endsWith("/")
  ? RAW_API_BASE.slice(0, -1)
  : RAW_API_BASE;

export const CONVERSATION_WINDOW_STORAGE_KEY = "float:conversation-window";
export const DEFAULT_CONVERSATION_MESSAGE_LIMIT = 80;
export const DEFAULT_CONVERSATION_TOOL_LIMIT = 40;

const CLIENT_CONVERSATION_TRIM_META = "__floatClientConversationTrim";
const SKIPPED_CONVERSATION_DETAIL_SEGMENTS = new Set([
  "export",
  "export-all",
  "import",
  "reveal",
  "suggest-name",
]);

const debugLogsEnabled = () => {
  try {
    if (typeof window !== "undefined" && window.__FLOAT_DEBUG_LOGS__ === true) {
      return true;
    }
    if (typeof localStorage !== "undefined") {
      return localStorage.getItem("floatDebugLogs") === "true";
    }
  } catch {
    return false;
  }
  return false;
};

export const debugLog = (...args) => {
  if (debugLogsEnabled()) {
    console.debug(...args);
  }
};

const parseBoundedInteger = (value, fallback, min, max) => {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
};

const getClientOverride = (windowKey, storageKey) => {
  try {
    if (typeof window !== "undefined" && window[windowKey] != null) {
      return window[windowKey];
    }
    if (typeof localStorage !== "undefined") {
      return localStorage.getItem(storageKey);
    }
  } catch {
    return null;
  }
  return null;
};

export const getConversationMessageLimit = () =>
  parseBoundedInteger(
    getClientOverride(
      "__FLOAT_CONVERSATION_MESSAGE_LIMIT__",
      "floatConversationMessageLimit",
    ) ?? import.meta.env.VITE_FLOAT_CONVERSATION_MESSAGE_LIMIT,
    DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    1,
    500,
  );

export const getConversationToolLimit = () =>
  parseBoundedInteger(
    getClientOverride(
      "__FLOAT_CONVERSATION_TOOL_LIMIT__",
      "floatConversationToolLimit",
    ) ?? import.meta.env.VITE_FLOAT_CONVERSATION_TOOL_LIMIT,
    DEFAULT_CONVERSATION_TOOL_LIMIT,
    1,
    200,
  );

const attachConversationTrimMeta = (target, meta) => {
  if (!target || typeof target !== "object" || !meta?.truncated) return target;
  try {
    Object.defineProperty(target, CLIENT_CONVERSATION_TRIM_META, {
      value: meta,
      enumerable: false,
      configurable: true,
    });
  } catch {
    target[CLIENT_CONVERSATION_TRIM_META] = meta;
  }
  return target;
};

export const getConversationTrimMeta = (target) => {
  if (!target || typeof target !== "object") return null;
  const meta = target[CLIENT_CONVERSATION_TRIM_META];
  return meta && typeof meta === "object" && meta.truncated ? meta : null;
};

const trimArrayTail = (items, limit) => {
  if (!Array.isArray(items) || items.length <= limit) {
    return { items, omitted: 0 };
  }
  return {
    items: items.slice(items.length - limit),
    omitted: items.length - limit,
  };
};

const trimMessageToolPayloads = (message, toolLimit) => {
  if (!message || typeof message !== "object") {
    return { message, omittedTools: 0 };
  }

  let nextMessage = message;
  let omittedTools = 0;
  const ensureCloned = () => {
    if (nextMessage === message) {
      nextMessage = { ...message };
    }
    return nextMessage;
  };

  const tools = Array.isArray(message.tools) ? message.tools : null;
  if (tools && tools.length > toolLimit) {
    const trimmed = trimArrayTail(tools, toolLimit);
    ensureCloned().tools = trimmed.items;
    omittedTools += trimmed.omitted;
  }

  const metadata = message.metadata && typeof message.metadata === "object"
    ? message.metadata
    : null;
  const inlinePayloads = Array.isArray(metadata?.inline_tool_payloads)
    ? metadata.inline_tool_payloads
    : null;
  if (inlinePayloads && inlinePayloads.length > toolLimit) {
    const trimmed = trimArrayTail(inlinePayloads, toolLimit);
    const clonedMessage = ensureCloned();
    clonedMessage.metadata = {
      ...metadata,
      inline_tool_payloads: trimmed.items,
    };
    omittedTools += trimmed.omitted;
  }

  if (omittedTools > 0) {
    const clonedMessage = ensureCloned();
    clonedMessage.metadata = {
      ...(clonedMessage.metadata || {}),
      client_trim: {
        ...(clonedMessage.metadata?.client_trim || {}),
        omitted_tools: omittedTools,
        tool_limit: toolLimit,
      },
    };
  }

  return { message: nextMessage, omittedTools };
};

export const trimConversationMessagesForDom = (
  messages,
  {
    source = "client",
    messageLimit = getConversationMessageLimit(),
    toolLimit = getConversationToolLimit(),
  } = {},
) => {
  if (!Array.isArray(messages)) {
    return { messages: [], meta: null };
  }

  const normalizedMessageLimit = parseBoundedInteger(
    messageLimit,
    DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    1,
    5000,
  );
  const normalizedToolLimit = parseBoundedInteger(
    toolLimit,
    DEFAULT_CONVERSATION_TOOL_LIMIT,
    1,
    1000,
  );
  const startIndex = Math.max(0, messages.length - normalizedMessageLimit);
  const windowedMessages = messages.slice(startIndex);
  let omittedTools = 0;
  let replacedMessage = false;
  const trimmedMessages = windowedMessages.map((message) => {
    const trimmed = trimMessageToolPayloads(message, normalizedToolLimit);
    omittedTools += trimmed.omittedTools;
    if (trimmed.message !== message) replacedMessage = true;
    return trimmed.message;
  });

  const omittedMessages = startIndex;
  if (omittedMessages <= 0 && omittedTools <= 0) {
    return { messages: replacedMessage ? trimmedMessages : messages, meta: null };
  }

  const nextMessages = omittedMessages > 0 || replacedMessage
    ? trimmedMessages
    : messages;
  const meta = {
    truncated: true,
    source,
    total_messages: messages.length,
    omitted_messages: omittedMessages,
    start_index: startIndex,
    message_limit: normalizedMessageLimit,
    omitted_tools: omittedTools,
    tool_limit: normalizedToolLimit,
  };
  attachConversationTrimMeta(nextMessages, meta);
  return { messages: nextMessages, meta };
};

const normalizeResponsePath = (url) => {
  if (typeof url !== "string" || !url.trim()) return "";
  try {
    return new URL(url, "http://float.local").pathname.replace(/\/+$/, "");
  } catch {
    return url.split("?", 1)[0].replace(/\/+$/, "");
  }
};

export const isConversationDetailRequest = (config = {}) => {
  const method = String(config.method || "get").toLowerCase();
  if (method !== "get") return false;
  const path = normalizeResponsePath(config.url || "");
  if (!path.startsWith("/api/conversations/")) return false;
  const rest = path.slice("/api/conversations/".length);
  if (!rest) return false;
  const segments = rest.split("/").filter(Boolean).map((segment) => {
    try {
      return decodeURIComponent(segment);
    } catch {
      return segment;
    }
  });
  if (!segments.length) return false;
  return !segments.some((segment) =>
    SKIPPED_CONVERSATION_DETAIL_SEGMENTS.has(String(segment).toLowerCase()),
  );
};

export const trimConversationResponseForDom = (response) => {
  if (!response || !isConversationDetailRequest(response.config)) {
    return response;
  }
  const data = response.data;
  if (!data || typeof data !== "object" || !Array.isArray(data.messages)) {
    return response;
  }
  const { messages, meta } = trimConversationMessagesForDom(data.messages, {
    source: "fetch",
  });
  if (!meta) return response;
  data.messages = messages;
  attachConversationTrimMeta(data, meta);
  debugLog("Trimmed conversation payload for DOM", meta);
  return response;
};

let responseTrimInterceptorInstalled = false;

export const installConversationResponseTrimInterceptor = () => {
  if (responseTrimInterceptorInstalled) return;
  const useInterceptor = axios?.interceptors?.response?.use;
  if (typeof useInterceptor !== "function") return;
  useInterceptor.call(
    axios.interceptors.response,
    trimConversationResponseForDom,
    (error) => Promise.reject(error),
  );
  responseTrimInterceptorInstalled = true;
};

installConversationResponseTrimInterceptor();

// Auto-decaying memory store (lightweight client cache)
export const memoryStore = new Proxy(
  {},
  {
    set(target, key, value) {
      debugLog(`Memory Updated: ${key}`, value);

      target[key] = {
        data: value,
        timestamp: Date.now(),
        decay: value.importance || 1, // Default importance if not set
      };

      return true;
    },
    get(target, key) {
      if (!target[key]) return null;

      // Decay function
      const timeElapsed = (Date.now() - target[key].timestamp) / 1000;
      target[key].decay *= Math.exp(-0.01 * timeElapsed);

      if (target[key].decay < 0.1) {
        debugLog(`Memory Expired: ${key}`);
        delete target[key];
        return null;
      }

      return target[key].data;
    },
  },
);

// Auto-wrapping API calls with optional abort support
export const apiWrapper = new Proxy(
  {},
  {
    get(_, endpoint) {
      return async (params = {}, options = {}) => {
        const { signal } = options || {};
        debugLog(`API Call: ${endpoint}`, params);

        try {
          const res = await axios.post(
            `${API_BASE_URL}/${endpoint}`,
            params,
            signal ? { signal } : undefined,
          );
          return res.data;
        } catch (err) {
          const cancelled =
            (signal && signal.aborted) ||
            err?.code === "ERR_CANCELED" ||
            err?.name === "CanceledError";
          if (cancelled) {
            return { cancelled: true };
          }
          console.error(`API Error (${endpoint}):`, err);
          const status = err?.response?.status;
          const payload = err?.response?.data;
          const detail =
            payload?.detail ||
            payload?.message ||
            payload?.error ||
            (status === 502
              ? "Request failed (502). The backend or dev proxy was unavailable for a moment."
              : err?.message) ||
            "API request failed";
          return { error: detail };
        }
      };
    },
  },
);
