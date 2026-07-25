export const MAX_CUSTOM_OUTPUT_TOKENS = 2_000_000;

export const OUTPUT_TOKEN_PRESETS = Object.freeze([
  { id: "4096", value: 4096, label: "4K" },
  { id: "8192", value: 8192, label: "8K" },
  { id: "16384", value: 16384, label: "16K" },
  { id: "32768", value: 32768, label: "32K" },
  { id: "65536", value: 65536, label: "64K" },
  { id: "131072", value: 131072, label: "128K" },
  { id: "262144", value: 262144, label: "256K" },
  { id: "524288", value: 524288, label: "512K" },
  { id: "1048576", value: 1048576, label: "1M" },
]);

const OUTPUT_TOKEN_PRESET_IDS = new Set(
  OUTPUT_TOKEN_PRESETS.map((preset) => preset.id),
);

const positiveInteger = (value, fallback = null) => {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(MAX_CUSTOM_OUTPUT_TOKENS, parsed);
};

export const normalizeOutputTokenMode = (value) => {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw || raw === "auto" || raw === "provider") return "auto";
  if (raw === "custom") return "custom";
  return OUTPUT_TOKEN_PRESET_IDS.has(raw) ? raw : "auto";
};

export const normalizeCustomOutputTokens = (value, fallback = 32768) =>
  positiveInteger(value, fallback);

export const selectedOutputTokenLimit = (mode, customValue) => {
  const normalizedMode = normalizeOutputTokenMode(mode);
  if (normalizedMode === "auto") return null;
  if (normalizedMode === "custom") {
    return normalizeCustomOutputTokens(customValue);
  }
  return positiveInteger(normalizedMode);
};

export const outputTokenPayload = (mode, customValue) => {
  const limit = selectedOutputTokenLimit(mode, customValue);
  return limit ? { max_output_tokens: limit } : {};
};

const capabilityNumber = (entry, ...keys) => {
  if (!entry || typeof entry !== "object") return null;
  for (const key of keys) {
    const parsed = positiveInteger(entry[key]);
    if (parsed) return parsed;
  }
  return null;
};

export const resolveModelCapabilities = (entries, modelId) => {
  const selected = String(modelId || "").trim();
  if (!selected || !Array.isArray(entries)) return null;
  const entry = entries.find((candidate) => {
    if (!candidate || typeof candidate !== "object") return false;
    const id = String(
      candidate.id ||
        candidate.model ||
        candidate.model_name ||
        candidate.name ||
        "",
    ).trim();
    return id === selected;
  });
  if (!entry) return null;
  return {
    id: selected,
    maxContextLength: capabilityNumber(
      entry,
      "max_context_length",
      "context_length",
      "context_window",
    ),
    maxOutputTokens: capabilityNumber(
      entry,
      "max_output_tokens",
      "max_completion_tokens",
      "output_token_limit",
    ),
    source: String(entry.source || entry.inventory_source || "").trim(),
    kind: String(entry.kind || "").trim(),
  };
};

export const formatTokenLimit = (value) => {
  const parsed = positiveInteger(value);
  if (!parsed) return "not reported";
  if (parsed >= 1048576 && parsed % 1048576 === 0) {
    return `${parsed / 1048576}M`;
  }
  if (parsed >= 1024 && parsed < 1048576 && parsed % 1024 === 0) {
    return `${parsed / 1024}K`;
  }
  if (parsed >= 1_000_000) {
    const millions = parsed / 1_000_000;
    return `${Number(millions.toFixed(millions >= 10 ? 0 : 2))}M`;
  }
  if (parsed >= 1000) {
    const thousands = parsed / 1000;
    return `${Number(thousands.toFixed(thousands >= 100 ? 0 : 1))}K`;
  }
  return String(parsed);
};
