export const DEFAULT_API_MODELS = [
  "gpt-4o",
  "gpt-5-mini",
  "gpt-5.1",
  "gpt-5",
  "gpt-5.2",
];

export const SUGGESTED_LOCAL_MODELS = [
  "gpt-oss-20b",
  "gpt-oss-120b",
  "Llama-3.1-8B",
  "Llama-3.1-70B",
  "Qwen3-8B",
  "Qwen3-235B-A22B-Instruct-2507",
  "mistral-7b-instruct-v0.3",
  "mixtral-8x7b-instruct-v0.1",
  "gemma-3-270m",
  "gemma-3-12b-it",
  "gemma-3-27b-it",
];

export const LOCAL_RUNTIME_ENTRIES = ["lmstudio", "ollama"];

const _cleanModelList = (list) =>
  (Array.isArray(list) ? list : [])
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);

export const normalizeModelId = (value) => {
  if (typeof value !== "string") return "";
  return value.trim().toLowerCase();
};

export const isLocalRuntimeEntry = (value) =>
  LOCAL_RUNTIME_ENTRIES.includes(normalizeModelId(value));

export const formatLocalRuntimeLabel = (value) => {
  const key = normalizeModelId(value);
  if (!key) return "";
  return `local/${key}`;
};

export const isGptOssModel = (value) => {
  const lowered = normalizeModelId(value);
  if (!lowered) return false;
  return (
    lowered.includes("gpt-oss-20b") ||
    lowered.includes("gpt-oss-120b") ||
    lowered.startsWith("gpt-oss-") ||
    lowered.includes("/gpt-oss-")
  );
};

export const buildModelGroups = ({ defaults = [], discovered = [], current = "" } = {}) => {
  const defaultsClean = _cleanModelList(defaults);
  const discoveredClean = _cleanModelList(discovered);

  const seen = new Set();
  const dedupe = (items) =>
    items.filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });

  const defaultModels = dedupe(defaultsClean);
  const extraModels = dedupe(discoveredClean.filter((m) => !defaultModels.includes(m)));

  const currentClean = typeof current === "string" ? current.trim() : "";
  if (currentClean && !defaultModels.includes(currentClean) && !extraModels.includes(currentClean)) {
    extraModels.unshift(currentClean);
  }

  return {
    defaults: defaultModels,
    extras: extraModels,
    all: [...defaultModels, ...extraModels],
  };
};
