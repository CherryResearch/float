export const stableValue = (value) => {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => stableValue(item));
  }
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce((acc, key) => {
        acc[key] = stableValue(value[key]);
        return acc;
      }, {});
  }
  return String(value);
};

const fnv1a = (input) => {
  let hash = 0x811c9dc5;
  const text = String(input || "");
  for (let idx = 0; idx < text.length; idx += 1) {
    hash ^= text.charCodeAt(idx);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
};

export const buildToolContinuationSignature = (tools, options = {}) => {
  const includeIds = options?.includeIds !== false;
  const normalized = (Array.isArray(tools) ? tools : [])
    .filter((tool) => tool && typeof tool === "object")
    .map((tool) => ({
      ...(includeIds
        ? {
            id:
              tool.id !== null && typeof tool.id !== "undefined"
                ? String(tool.id)
                : tool.request_id !== null && typeof tool.request_id !== "undefined"
                  ? String(tool.request_id)
                  : null,
          }
        : {}),
      name:
        typeof tool.name === "string"
          ? tool.name.trim()
          : typeof tool.tool === "string"
            ? tool.tool.trim()
            : "",
      status: typeof tool.status === "string" ? tool.status.trim().toLowerCase() : "",
      args:
        tool.args && typeof tool.args === "object" && !Array.isArray(tool.args)
          ? stableValue(tool.args)
          : {},
      result: Object.prototype.hasOwnProperty.call(tool, "result")
        ? stableValue(tool.result)
        : null,
    }))
    .filter((tool) => (includeIds ? tool.id || tool.name : tool.name));
  if (!normalized.length) return "";
  try {
    return fnv1a(JSON.stringify(normalized));
  } catch {
    return "";
  }
};

const activeToolContinuationLocks = new Set();

export const buildToolContinuationLockKey = ({
  sessionId,
  messageId,
  tools,
} = {}) => {
  const sessionPart = String(sessionId || "").trim() || "default";
  const messagePart = String(messageId || "").trim();
  const semanticSignature = buildToolContinuationSignature(tools, {
    includeIds: false,
  });
  const exactSignature = semanticSignature || buildToolContinuationSignature(tools);
  if (!messagePart || !exactSignature) return "";
  return `${sessionPart}:${messagePart}:${exactSignature}`;
};

export const acquireToolContinuationLock = (key) => {
  const normalized = String(key || "").trim();
  if (!normalized) return false;
  if (activeToolContinuationLocks.has(normalized)) return false;
  activeToolContinuationLocks.add(normalized);
  return true;
};

export const releaseToolContinuationLock = (key) => {
  const normalized = String(key || "").trim();
  if (!normalized) return;
  activeToolContinuationLocks.delete(normalized);
};

export const hasMatchingToolContinuationSignature = (
  metadata,
  tools,
  options = {},
) => {
  if (!metadata || typeof metadata !== "object" || metadata.unresolved_tool_loop) {
    return false;
  }
  const includeIds = options?.includeIds !== false;
  const metadataKey =
    typeof options?.metadataKey === "string" && options.metadataKey.trim()
      ? options.metadataKey.trim()
      : includeIds
        ? "tool_continue_signature"
        : "tool_continue_semantic_signature";
  const current = buildToolContinuationSignature(tools, { includeIds });
  const saved =
    typeof metadata[metadataKey] === "string"
      ? metadata[metadataKey].trim().toLowerCase()
      : "";
  return Boolean(current && saved && current === saved);
};
