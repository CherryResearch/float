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
