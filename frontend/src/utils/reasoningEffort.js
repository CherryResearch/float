export const REASONING_EFFORT_PRESETS = Object.freeze([
  { id: "none", value: 0, label: "none" },
  { id: "minimal", value: 0.01, label: "minimal" },
  { id: "low", value: 0.3, label: "low" },
  { id: "medium", value: 0.6, label: "medium" },
  { id: "high", value: 0.9, label: "high" },
  { id: "xhigh", value: 0.99, label: "xhigh" },
]);

const PRESET_IDS = new Set(REASONING_EFFORT_PRESETS.map((preset) => preset.id));
const PRESET_VALUES = new Map(
  REASONING_EFFORT_PRESETS.map((preset) => [preset.id, preset.value]),
);

const normalizeNumericEffort = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.min(0.99, Math.max(0, numeric));
};

export const normalizeThinkingMode = (value) => {
  if (value === true || String(value).trim().toLowerCase() === "true") return "high";
  if (value === false || String(value).trim().toLowerCase() === "false") return "low";
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw || raw === "auto") return "auto";
  if (raw === "off" || raw === "disabled") return "none";
  if (raw === "max") return "xhigh";
  if (PRESET_IDS.has(raw)) return raw;
  const numeric = normalizeNumericEffort(raw);
  if (numeric === null) return "auto";
  return String(Number(numeric.toFixed(2)));
};

export const thinkingPayloadForMode = (value) => {
  const normalized = normalizeThinkingMode(value);
  if (normalized === "auto") return {};
  if (PRESET_IDS.has(normalized)) return { thinking: normalized };
  const numeric = normalizeNumericEffort(normalized);
  return numeric === null ? {} : { thinking: numeric };
};

export const reasoningEffortValue = (value, fallback = 0.9) => {
  const normalized = normalizeThinkingMode(value);
  if (PRESET_VALUES.has(normalized)) return PRESET_VALUES.get(normalized);
  const numeric = normalizeNumericEffort(normalized);
  return numeric === null ? fallback : numeric;
};

export const isCustomReasoningEffort = (value) => {
  const normalized = normalizeThinkingMode(value);
  return normalized !== "auto" && !PRESET_IDS.has(normalized);
};
