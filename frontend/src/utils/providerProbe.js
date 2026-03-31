export const API_PROVIDER_MODELS_REFRESH_MS = 15 * 60 * 1000;

export const shouldRefreshProviderModels = (probeState, now = Date.now()) => {
  const providerStatus = probeState?.apiProviderStatus ?? "unknown";
  const modelsUpdatedAt = Number(probeState?.apiModelsUpdatedAt) || 0;
  if (providerStatus !== "online") {
    return true;
  }
  if (!modelsUpdatedAt) {
    return true;
  }
  return now - modelsUpdatedAt >= API_PROVIDER_MODELS_REFRESH_MS;
};
