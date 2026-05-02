import { isLikelyEmbeddingModelName } from "./modelFiltering";

export const cleanProviderModelName = (value) =>
  typeof value === "string" ? value.trim() : "";

export const isChatCapableProviderModelName = (value) => {
  const modelName = cleanProviderModelName(value);
  return Boolean(modelName) && !isLikelyEmbeddingModelName(modelName);
};

export const filterChatCapableProviderModels = (models) => {
  if (!Array.isArray(models)) return [];
  const seen = new Set();
  const filtered = [];
  for (const rawEntry of models) {
    const entry = cleanProviderModelName(rawEntry);
    if (!isChatCapableProviderModelName(entry) || seen.has(entry)) continue;
    seen.add(entry);
    filtered.push(entry);
  }
  return filtered;
};

export const providerRuntimeHasChatModel = (runtime) => {
  if (!runtime || typeof runtime !== "object") return false;
  if (runtime.model_loaded) return true;
  return (
    isChatCapableProviderModelName(runtime.effective_model_id) ||
    isChatCapableProviderModelName(runtime.effective_model) ||
    isChatCapableProviderModelName(runtime.loaded_model)
  );
};

const normalizeProviderRuntimeTimestamp = (value) => {
  if (value == null || value === "") return null;
  const raw = Number(value);
  const normalized = Number.isFinite(raw)
    ? raw > 0 && raw < 1e12
      ? raw * 1000
      : raw
    : new Date(value).getTime();
  return Number.isFinite(normalized) ? normalized : null;
};

const formatProviderRuntimeRelativeTime = (value, now = Date.now()) => {
  const normalized = normalizeProviderRuntimeTimestamp(value);
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

const formatProviderRuntimeDuration = (value) => {
  const durationMs = Number(value);
  if (!Number.isFinite(durationMs) || durationMs < 0) return null;
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`;
  if (durationMs < 10000) return `${(durationMs / 1000).toFixed(1)}s`;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
};

const truncateProviderOperationText = (value, limit = 72) => {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
};

const humanizeProviderOperationAction = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return "operation";
  switch (normalized) {
    case "set-target":
      return "set target";
    default:
      return normalized.replace(/[-_]+/g, " ");
  }
};

const normalizeProviderOperationStatus = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return "done";
  if (normalized === "ok") return "ok";
  if (normalized === "error") return "failed";
  return normalized;
};

const summarizeProviderOperationReason = (result) => {
  if (!result || typeof result !== "object") return "";
  const note = truncateProviderOperationText(String(result.note || ""));
  if (note) return note;
  const error = truncateProviderOperationText(String(result.error || ""));
  if (error) return error;
  const preview = truncateProviderOperationText(String(result.stdout_preview || ""));
  if (preview) return preview;
  const endpoint = truncateProviderOperationText(
    String(result.endpoint || result.base_url || ""),
  );
  if (endpoint) return endpoint;
  if (Array.isArray(result.targets) && result.targets.length) {
    return truncateProviderOperationText(
      `targets ${result.targets
        .slice(0, 2)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .join(", ")}`,
    );
  }
  const cmd = truncateProviderOperationText(String(result.cmd || ""));
  if (cmd) return cmd;
  const binary = truncateProviderOperationText(String(result.binary || ""));
  if (binary) return binary;
  if (Number.isFinite(result.pid) && result.pid > 0) {
    return `pid ${result.pid}`;
  }
  return "";
};

export const formatProviderLastOperation = (operation, now = Date.now()) => {
  if (!operation || typeof operation !== "object") return null;
  const action = humanizeProviderOperationAction(operation.action);
  const operationId = String(operation.id || "").trim();
  const status = normalizeProviderOperationStatus(operation.status);
  const model = cleanProviderModelName(operation.model);
  const startedAgo = formatProviderRuntimeRelativeTime(operation.started_at, now);
  const finishedAgo = formatProviderRuntimeRelativeTime(operation.finished_at, now);
  const duration = formatProviderRuntimeDuration(operation.duration_ms);
  const result =
    operation.result && typeof operation.result === "object" ? operation.result : {};
  const reason = summarizeProviderOperationReason(result);

  let summary = `${operationId || action} ${status}`;
  if (model) {
    summary += ` for ${model}`;
  }
  if (status === "running") {
    if (startedAgo) {
      summary += `, started ${startedAgo}`;
    }
  } else {
    if (finishedAgo) {
      summary += `, finished ${finishedAgo}`;
    } else if (startedAgo) {
      summary += `, started ${startedAgo}`;
    }
    if (duration) {
      summary += ` (${duration})`;
    }
  }
  if (reason && (status === "failed" || status === "running")) {
    summary += `, ${reason}`;
  }

  const titleParts = [`${action}${operationId ? ` (${operationId})` : ""}`, `status ${status}`];
  if (model) titleParts.push(`model ${model}`);
  if (startedAgo) titleParts.push(`started ${startedAgo}`);
  if (finishedAgo && status !== "running") titleParts.push(`finished ${finishedAgo}`);
  if (duration) titleParts.push(`duration ${duration}`);
  if (reason) titleParts.push(reason);
  if (Array.isArray(result.targets) && result.targets.length) {
    titleParts.push(
      `targets ${result.targets
        .slice(0, 3)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .join(", ")}`,
    );
  }
  if (result.endpoint) titleParts.push(`endpoint ${String(result.endpoint).trim()}`);
  if (result.base_url) titleParts.push(`base ${String(result.base_url).trim()}`);
  if (result.cmd) titleParts.push(`cmd ${truncateProviderOperationText(String(result.cmd), 140)}`);
  if (Number.isFinite(result.pid) && result.pid > 0) titleParts.push(`pid ${result.pid}`);

  return {
    label: `Last action: ${summary}`,
    title: titleParts.filter(Boolean).join(" • "),
    status,
  };
};
