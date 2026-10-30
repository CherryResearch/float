import { ROLLING_API_MODEL_ALIAS_LABELS } from "./modelUtils.js";

const cleanString = (value) => typeof value === "string" ? value.trim() : "";
const aliasLabel = (value) =>
  ROLLING_API_MODEL_ALIAS_LABELS[cleanString(value).toLowerCase()] || "";

export const getMessageModelLabel = (metadata = {}) => {
  const requested = cleanString(metadata.model_requested) || cleanString(metadata.model);
  const candidates = [
    metadata.model_received,
    metadata.model_resolved,
    metadata.effective_model_id,
    metadata.effective_model,
  ].map(cleanString).filter(Boolean);
  const reported = candidates.find((model) => !aliasLabel(model));
  const alias = aliasLabel(requested) || aliasLabel(candidates[0]);
  if (alias) {
    // Only per-response evidence can identify what served a rolling alias.
    return reported ? `${alias} (${reported})` : `${alias} (model not reported)`;
  }
  return reported || candidates[0] || requested;
};

const timestampMillis = (value) => {
  if (!["number", "string"].includes(typeof value) || value === "") return null;
  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value.trim())
      ? Number(value)
      : null;
  const normalized = Number.isFinite(numeric)
    ? Math.abs(numeric) < 1e12 ? numeric * 1000 : numeric
    : value;
  const millis = new Date(normalized).getTime();
  return Number.isFinite(millis) ? millis : null;
};

const formatTimestamp = (millis) => new Date(millis).toLocaleString([], {
  dateStyle: "medium",
  timeStyle: "medium",
});

export const getMessageTimingRows = (message = {}) => {
  const timing = message.metadata?.timing || {};
  const sent = timestampMillis(timing.sent_at);
  const accepted = timestampMillis(timing.accepted_at);
  const firstOutput = timestampMillis(timing.first_output_at);
  const completed = timestampMillis(timing.completed_at);
  const rows = [
    ["Sent", sent],
    ["Accepted", accepted],
    ["First output", firstOutput],
    ["Completed", completed],
  ].filter(([, millis]) => millis !== null)
    .map(([label, millis]) => ({ label, value: formatTimestamp(millis) }));
  if (rows.length === 0) {
    // Legacy timestamps can be placeholder creation times. updated_at is also
    // changed by edits and continuations, so neither proves response completion.
    const legacyTime = timestampMillis(message.timestamp);
    return legacyTime === null
      ? []
      : [{ label: "Message time", value: formatTimestamp(legacyTime) }];
  }
  const start = accepted ?? sent;
  if (start !== null && completed !== null && completed >= start) {
    const seconds = (completed - start) / 1000;
    rows.push({
      label: "Elapsed",
      value: `${seconds.toFixed(1)} s${message.metadata?.tool_continuation_rounds > 0 ? " (including tool wait)" : ""}`,
    });
  }
  return rows;
};

export const getMessageUsageLabel = (usage) => {
  const source = cleanString(usage?.source).toLowerCase();
  if (source === "estimate") return "Usage (estimated)";
  if (source === "mixed") return "Usage (partly estimated)";
  return "Usage";
};

export const getProviderReasoningTokens = (usage) => {
  if (!usage || typeof usage !== "object" || cleanString(usage.source).toLowerCase() === "estimate") {
    return null;
  }
  const count = usage.output_tokens_details?.reasoning_tokens
    ?? usage.completion_tokens_details?.reasoning_tokens;
  return Number.isInteger(count) && count >= 0 ? count : null;
};
