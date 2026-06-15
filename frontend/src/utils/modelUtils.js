export const DEFAULT_API_MODELS = [
  "gpt-5.4",
  "gpt-5.4-mini",
  "gpt-5.4-nano",
];

const MODEL_SIZE_RANK = {
  base: 5,
  chat: 4,
  codex: 3,
  pro: 2,
  max: 1,
  mini: -1,
  nano: -2,
};

const DIRECT_LOCAL_GEMMA_MODELS = new Set([
  "gemma-4-E2B-it",
  "gemma-4-E2B-it-qat-q4_0",
  "gemma-4-12B-it-qat-q4_0",
]);

const PROVIDER_FIRST_GEMMA_MODELS = new Set([
  "gemma-4-E2B-it-qat-q4_0-gguf",
  "gemma-4-E4B-it-qat-q4_0-gguf",
  "gemma-4-12B-it-qat-q4_0-gguf",
  "gemma-4-26B-A4B-it-qat-q4_0-gguf",
  "gemma-4-31B-it-qat-q4_0-gguf",
  "gemma-4-12B-it",
  "gemma-4-E4B-it",
  "gemma-4-26B-A4B-it",
  "gemma-4-31B-it",
]);

const DOWNLOADABLE_PROVIDER_MODELS = new Set([
  "gemma-4-E2B-it-qat-q4_0-gguf",
  "gemma-4-E4B-it-qat-q4_0-gguf",
  "gemma-4-12B-it-qat-q4_0-gguf",
  "gemma-4-26B-A4B-it-qat-q4_0-gguf",
  "gemma-4-31B-it-qat-q4_0-gguf",
]);

const DOWNLOADABLE_UTILITY_MODELS = new Set([
  "all-MiniLM-L6-v2",
  "all-mpnet-base-v2",
  "embeddinggemma-300m",
  "privacy-filter",
]);

const DIRECT_LOCAL_GEMMA_MODEL_IDS = new Set(
  Array.from(DIRECT_LOCAL_GEMMA_MODELS).map((model) => model.toLowerCase()),
);
const PROVIDER_FIRST_GEMMA_MODEL_IDS = new Set(
  Array.from(PROVIDER_FIRST_GEMMA_MODELS).map((model) => model.toLowerCase()),
);
const DOWNLOADABLE_PROVIDER_MODEL_IDS = new Set(
  Array.from(DOWNLOADABLE_PROVIDER_MODELS).map((model) => model.toLowerCase()),
);

export const SUGGESTED_LOCAL_MODELS = [
  "gpt-oss-20b",
  "gpt-oss-120b",
  "Llama-3.1-8B",
  "Llama-3.1-70B",
  "Qwen3-8B",
  "Qwen3-235B-A22B-Instruct-2507",
  "mistral-7b-instruct-v0.3",
  "mixtral-8x7b-instruct-v0.1",
  "gemma-4-E2B-it",
  "gemma-4-E2B-it-qat-q4_0",
  "gemma-4-12B-it-qat-q4_0",
];

export const SUGGESTED_SERVER_MODELS = [
  ...SUGGESTED_LOCAL_MODELS,
  "gemma-4-E2B-it-qat-q4_0-gguf",
  "gemma-4-E4B-it-qat-q4_0-gguf",
  "gemma-4-12B-it-qat-q4_0-gguf",
  "gemma-4-26B-A4B-it-qat-q4_0-gguf",
  "gemma-4-31B-it-qat-q4_0-gguf",
  "gemma-4-12B-it",
  "gemma-4-E4B-it",
  "gemma-4-26B-A4B-it",
  "gemma-4-31B-it",
];

export const LOCAL_RUNTIME_ENTRIES = [
  "lmstudio",
  "ollama",
  "custom-openai-compatible",
];

const _cleanModelList = (list) =>
  (Array.isArray(list) ? list : [])
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);

const _cleanModelValue = (value) =>
  typeof value === "string" ? value.trim() : "";

const _parseModelDate = (value) => {
  const match = String(value || "").match(/(?:^|-)(20\d{2})-(\d{2})-(\d{2})(?:$|-)/);
  if (!match) return 0;
  return Number(`${match[1]}${match[2]}${match[3]}`);
};

const _parseGptSortKey = (value) => {
  const raw = _cleanModelValue(value);
  const lowered = raw.toLowerCase();
  const match = lowered.match(/^gpt-(\d+)(?:\.(\d+))?/);
  if (!match) return null;
  const suffix = lowered.slice(match[0].length).replace(/^-+/, "");
  const size =
    suffix.split("-").find((part) =>
      Object.prototype.hasOwnProperty.call(MODEL_SIZE_RANK, part),
    ) || "base";
  return {
    major: Number(match[1]) || 0,
    minor: Number(match[2]) || 0,
    date: _parseModelDate(lowered),
    sizeRank: MODEL_SIZE_RANK[size] ?? MODEL_SIZE_RANK.base,
    raw,
  };
};

export const compareModelIds = (left, right) => {
  const leftClean = _cleanModelValue(left);
  const rightClean = _cleanModelValue(right);
  const leftGpt = _parseGptSortKey(leftClean);
  const rightGpt = _parseGptSortKey(rightClean);
  if (leftGpt || rightGpt) {
    if (!leftGpt) return 1;
    if (!rightGpt) return -1;
    if (rightGpt.major !== leftGpt.major) return rightGpt.major - leftGpt.major;
    if (rightGpt.minor !== leftGpt.minor) return rightGpt.minor - leftGpt.minor;
    if (rightGpt.sizeRank !== leftGpt.sizeRank) {
      return rightGpt.sizeRank - leftGpt.sizeRank;
    }
    if (rightGpt.date !== leftGpt.date) return rightGpt.date - leftGpt.date;
  }
  return leftClean.localeCompare(rightClean, undefined, {
    numeric: true,
    sensitivity: "base",
  });
};

export const sortModelIds = (models) => _cleanModelList(models).sort(compareModelIds);

export const normalizeModelId = (value) => {
  if (typeof value !== "string") return "";
  return value.trim().toLowerCase();
};

export const isLocalRuntimeEntry = (value) =>
  LOCAL_RUNTIME_ENTRIES.includes(normalizeModelId(value));

export const formatLocalRuntimeLabel = (value) => {
  const key = normalizeModelId(value);
  if (!key) return "";
  if (key === "custom-openai-compatible") {
    return "local/openai-compatible";
  }
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

export const isGemmaFamilyModel = (value) => {
  const lowered = normalizeModelId(value);
  return lowered.startsWith("gemma-");
};

export const isDirectLocalGemmaModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return DIRECT_LOCAL_GEMMA_MODELS.has(raw) || DIRECT_LOCAL_GEMMA_MODEL_IDS.has(raw.toLowerCase());
};

export const isProviderFirstGemmaModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return PROVIDER_FIRST_GEMMA_MODELS.has(raw) || PROVIDER_FIRST_GEMMA_MODEL_IDS.has(raw.toLowerCase());
};

export const isKnownDirectDownloadModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return SUGGESTED_LOCAL_MODELS.includes(raw) || DIRECT_LOCAL_GEMMA_MODELS.has(raw);
};

export const isKnownDownloadableModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  const normalized = raw.toLowerCase();
  return (
    isKnownDirectDownloadModel(raw)
    || DOWNLOADABLE_PROVIDER_MODELS.has(raw)
    || DOWNLOADABLE_PROVIDER_MODEL_IDS.has(normalized)
    || DOWNLOADABLE_UTILITY_MODELS.has(raw)
    || DOWNLOADABLE_UTILITY_MODELS.has(normalized)
  );
};

export const resolveLocalCatalogModelId = (value) => {
  const raw = _cleanModelValue(value);
  if (!raw) return "";
  const withoutLane = /^[a-z]+:/i.test(raw) ? raw.split(":").slice(1).join(":").trim() : raw;
  if (!withoutLane.includes("/")) return withoutLane;
  const tail = withoutLane.split("/").filter(Boolean).pop();
  return tail ? tail.trim() : raw;
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

  const hasDiscoveredModels = discoveredClean.length > 0;
  const primaryModels = hasDiscoveredModels ? discoveredClean : defaultsClean;
  const defaultModels = dedupe(sortModelIds(primaryModels));
  const extraModels = [];

  const currentClean = typeof current === "string" ? current.trim() : "";
  if (currentClean && !seen.has(currentClean)) {
    seen.add(currentClean);
    extraModels.unshift(currentClean);
  }

  return {
    defaults: defaultModels,
    extras: extraModels,
    all: [...defaultModels, ...extraModels],
    source: hasDiscoveredModels ? "discovered" : "defaults",
  };
};

export const resolveConfiguredLocalModel = (state = {}) => {
  const localModel = _cleanModelValue(state?.localModel);
  const transformerModel = _cleanModelValue(state?.transformerModel);
  if (isLocalRuntimeEntry(transformerModel) && !isLocalRuntimeEntry(localModel)) {
    return transformerModel;
  }
  if (isLocalRuntimeEntry(localModel) && !isLocalRuntimeEntry(transformerModel)) {
    return localModel;
  }
  return transformerModel || localModel || "";
};

export const resolveSelectedLocalModel = (state = {}) => {
  const localModel = resolveConfiguredLocalModel(state);
  const transformerModel = _cleanModelValue(state?.transformerModel);

  if (transformerModel && !isLocalRuntimeEntry(transformerModel)) {
    return transformerModel;
  }
  if (localModel && !isLocalRuntimeEntry(localModel)) {
    return localModel;
  }
  return localModel || transformerModel || "";
};

export const resolveConcreteModelSelection = (value) => {
  const model = _cleanModelValue(value);
  if (!model || isLocalRuntimeEntry(model)) {
    return "";
  }
  return model;
};

export const resolveRuntimeModelLabel = ({ state = {}, runtime = null } = {}) => {
  const configuredLocalModel = resolveConfiguredLocalModel(state);
  if (configuredLocalModel && isLocalRuntimeEntry(configuredLocalModel)) {
    const runtimeModel =
      _cleanModelValue(runtime?.effective_model_id) ||
      _cleanModelValue(runtime?.loaded_model) ||
      _cleanModelValue(runtime?.model);
    return runtimeModel || configuredLocalModel;
  }
  const selectedLocalModel = resolveSelectedLocalModel(state);
  if (selectedLocalModel) {
    return selectedLocalModel;
  }
  return (
    _cleanModelValue(runtime?.effective_model_id) ||
    _cleanModelValue(runtime?.model) ||
    ""
  );
};

export const resolveModelForMode = ({
  backendMode = "api",
  apiModel = "",
  transformerModel = "",
  localModel = "",
} = {}) => {
  const mode = normalizeModelId(backendMode) || "api";
  const api = _cleanModelValue(apiModel);
  const transformer = _cleanModelValue(transformerModel);
  const local = _cleanModelValue(localModel);

  if (mode === "local") {
    const configuredLocal = resolveConfiguredLocalModel({
      localModel: local,
      transformerModel: transformer,
    });
    return configuredLocal || api;
  }
  if (mode === "server") {
    return isLocalRuntimeEntry(transformer) ? "" : transformer;
  }
  return api;
};

export const resolveRequestModelForMode = ({
  backendMode = "api",
  apiModel = "",
  transformerModel = "",
  localModel = "",
} = {}) => {
  const mode = normalizeModelId(backendMode) || "api";
  const api = _cleanModelValue(apiModel);
  const transformer = _cleanModelValue(transformerModel);
  const local = _cleanModelValue(localModel);

  if (mode === "local") {
    if (isLocalRuntimeEntry(local)) {
      return "";
    }
    const selectedLocal = resolveSelectedLocalModel({
      localModel: local,
      transformerModel: transformer,
    });
    return resolveConcreteModelSelection(selectedLocal);
  }
  if (mode === "server") {
    return resolveConcreteModelSelection(transformer);
  }
  if (mode === "api") {
    return api;
  }
  return api || resolveConcreteModelSelection(local) || resolveConcreteModelSelection(transformer);
};
