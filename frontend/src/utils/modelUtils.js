export const DEFAULT_API_MODELS = [
  "gpt-5.4",
  "gpt-5.4-mini",
  "gpt-5.4-nano",
];

const DIRECT_LOCAL_GEMMA_MODELS = new Set([
  "gemma-3",
  "gemma-3-270m",
  "gemma-3-12b-it",
  "gemma-3-27b-it",
  "gemma-4-E2B-it",
]);

const PROVIDER_FIRST_GEMMA_MODELS = new Set([
  "gemma-4-E4B-it",
  "gemma-4-26B-A4B-it",
  "gemma-4-31B-it",
]);

const DOWNLOADABLE_PROVIDER_MODELS = new Set([
  "gemma-4-E4B-it",
]);

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
  "gemma-4-E2B-it",
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
  return DIRECT_LOCAL_GEMMA_MODELS.has(raw);
};

export const isProviderFirstGemmaModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return PROVIDER_FIRST_GEMMA_MODELS.has(raw);
};

export const isKnownDirectDownloadModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return SUGGESTED_LOCAL_MODELS.includes(raw) || DIRECT_LOCAL_GEMMA_MODELS.has(raw);
};

export const isKnownDownloadableModel = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return false;
  return isKnownDirectDownloadModel(raw) || DOWNLOADABLE_PROVIDER_MODELS.has(raw);
};

export const resolveLocalCatalogModelId = (value) => {
  const raw = _cleanModelValue(value);
  if (!raw) return "";
  if (!raw.includes("/")) return raw;
  const tail = raw.split("/").filter(Boolean).pop();
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
    return isLocalRuntimeEntry(transformer) ? api : transformer || api;
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
    return (
      resolveConcreteModelSelection(transformer) ||
      resolveConcreteModelSelection(local)
    );
  }
  if (mode === "api") {
    return api;
  }
  return api || resolveConcreteModelSelection(local) || resolveConcreteModelSelection(transformer);
};
