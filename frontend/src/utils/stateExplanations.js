const cleanExplanationText = (value, limit = 320) => {
  if (value === null || typeof value === "undefined") return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => cleanExplanationText(item, limit)).filter(Boolean).join(", ");
  }
  if (typeof value === "object") {
    return cleanExplanationText(value.message || value.detail || value.label || "", limit);
  }
  const text = String(value).replace(/\s+/g, " ").trim();
  if (!text || text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
};

export const normalizeStateExplanationRows = (rows = []) =>
  (Array.isArray(rows) ? rows : [])
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const label = cleanExplanationText(row.label, 80);
      const value = cleanExplanationText(row.value);
      if (!label || !value) return null;
      return { label, value };
    })
    .filter(Boolean);

export const getStateExplanation = (payload) => {
  if (!payload || typeof payload !== "object") return null;
  const explanation = payload.state_explanation || payload.stateExplanation || payload.explanation;
  return explanation && typeof explanation === "object" ? explanation : null;
};

export const extractStateExplanationMessage = (
  detail,
  fallback = "Request failed.",
) => {
  if (typeof detail === "string") return cleanExplanationText(detail, 1000) || fallback;
  if (!detail || typeof detail !== "object") return fallback;
  return (
    cleanExplanationText(detail.message || detail.detail || detail.error, 1000) ||
    fallback
  );
};

export const buildStateExplanationRows = (payload, fallbackRows = []) => {
  const explanation = getStateExplanation(payload);
  const rows = normalizeStateExplanationRows(explanation?.rows);
  return rows.length ? rows : normalizeStateExplanationRows(fallbackRows);
};

export const getStateExplanationTitle = (
  payload,
  fallback = "Why am I seeing this?",
) => cleanExplanationText(getStateExplanation(payload)?.title, 120) || fallback;

export const getStateExplanationSummary = (payload, fallback = "") =>
  cleanExplanationText(getStateExplanation(payload)?.summary, 500) || fallback;

export const buildModelDeleteLockInspectorRows = (detail, fallbackModel = "") => {
  const rows = buildStateExplanationRows(detail);
  if (rows.length) return rows;
  const message = extractStateExplanationMessage(detail, "Model deletion failed.");
  return normalizeStateExplanationRows([
    { label: "Source", value: "model delete guard" },
    { label: "Model", value: fallbackModel },
    { label: "Evidence", value: message },
    {
      label: "Next",
      value: "Unload or stop the runtime that owns this model, then retry delete.",
    },
  ]);
};

export const buildProviderRuntimeInspectorRows = ({
  providerKey = "",
  providerLabel = "",
  providerRuntime,
  status = "",
  summary = "",
  detail = "",
  ownershipWarning = "",
  lastOperation,
  actionMessage = "",
} = {}) => {
  const runtime = providerRuntime && typeof providerRuntime === "object" ? providerRuntime : {};
  const backendRows = buildStateExplanationRows(runtime);
  if (backendRows.length) return backendRows;
  const loadedModel = cleanExplanationText(runtime.loaded_model || runtime.effective_model_id);
  const endpoint = cleanExplanationText(runtime.base_url);
  const owner =
    runtime.server_owned_by_float === false || runtime.loaded_model_owned_by_float === false
      ? "outside Float"
      : "Float";
  return normalizeStateExplanationRows([
    { label: "Source", value: "/api/llm/provider/status" },
    { label: "Provider", value: providerLabel || providerKey || runtime.provider },
    { label: "Status", value: status },
    { label: "Owner", value: owner },
    { label: "Model", value: loadedModel },
    { label: "Endpoint", value: endpoint },
    { label: "Summary", value: summary },
    { label: "Last action", value: lastOperation?.label },
    { label: "Evidence", value: ownershipWarning || detail || actionMessage },
    {
      label: "Next",
      value:
        ownershipWarning ||
        actionMessage ||
        "Refresh provider status or use runtime controls to change the active model.",
    },
  ]);
};

export const buildSyncOperationInspectorRows = (operation) => {
  const rows = buildStateExplanationRows(operation);
  if (rows.length) return rows;
  if (!operation || typeof operation !== "object") return [];
  const remote = cleanExplanationText(operation.remote_label || operation.remote_url);
  const status = cleanExplanationText(operation.status, 80).toLowerCase();
  const defaultNext =
    status === "running" || status === "cancel_requested" || operation.cancel_requested
      ? "Stop records cancel intent and aborts the current local request where supported."
      : "Recent sync attempts stay visible so retries can explain what changed.";
  return normalizeStateExplanationRows([
    { label: "Source", value: "sync operation ledger" },
    { label: "Operation", value: [operation.kind, operation.id].filter(Boolean).join(" / ") },
    { label: "Status", value: operation.status },
    { label: "Owner", value: operation.owner || "this device" },
    { label: "Remote", value: remote },
    { label: "Sections", value: operation.sections },
    { label: "Evidence", value: operation.error },
    {
      label: "Next",
      value: operation.stop_effect || defaultNext,
    },
  ]);
};

export const buildSyncOwnershipInspectorRows = ({
  syncOwnershipSummary,
  activeOperation,
  lastOperation,
  activeDescription = "none",
  lastDescription = "none recorded",
} = {}) => {
  const summary =
    syncOwnershipSummary && typeof syncOwnershipSummary === "object"
      ? syncOwnershipSummary
      : {};
  const operationRows = buildSyncOperationInspectorRows(activeOperation || lastOperation);
  const operationEvidence = operationRows
    .filter((row) => !["Source", "Owner", "Status", "Next"].includes(row.label))
    .slice(0, 3)
    .map((row) => `${row.label}: ${row.value}`)
    .join(" | ");
  const nextFromOperation = operationRows.find((row) => row.label === "Next")?.value;
  return normalizeStateExplanationRows([
    { label: "Source", value: "/api/sync/overview" },
    {
      label: "Owner",
      value: cleanExplanationText(summary.source_namespace) || "this device",
    },
    {
      label: "Outbound",
      value: cleanExplanationText(summary.default_target_label || summary.default_target_url),
    },
    { label: "Active", value: activeDescription },
    { label: "Last", value: lastDescription },
    { label: "Evidence", value: operationEvidence },
    {
      label: "Next",
      value:
        nextFromOperation ||
        cleanExplanationText(summary.unfinished_notice) ||
        "Review before pushing or pulling remote changes.",
    },
  ]);
};
