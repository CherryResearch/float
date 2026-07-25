export const GROK_TRUST_WARNING = "This model may not be trustworthy.";

const normalizeText = (value) => String(value || "").trim();

export const normalizeServerPreset = (value = {}) => {
  const provider = normalizeText(value.provider || "openai-compatible").toLowerCase();
  const name = normalizeText(value.name || "Custom endpoint");
  const trustWarning =
    provider === "xai" || name.toLowerCase().includes("grok")
      ? GROK_TRUST_WARNING
      : normalizeText(value.trust_warning);
  return {
    id: normalizeText(value.id),
    name,
    provider,
    base_url: normalizeText(value.base_url),
    api_key_env: normalizeText(value.api_key_env).toUpperCase(),
    description: normalizeText(value.description),
    builtin: !!value.builtin,
    api_key_set: !!value.api_key_set,
    ...(trustWarning ? { trust_warning: trustWarning } : {}),
  };
};

export const normalizeServerPresets = (values) =>
  (Array.isArray(values) ? values : [])
    .map(normalizeServerPreset)
    .filter((preset) => preset.id && preset.name);

export const selectedServerPreset = (settings = {}) => {
  const presets = normalizeServerPresets(settings.server_presets);
  const requestedId = normalizeText(settings.server_preset_id);
  const selected = presets.find((preset) => preset.id === requestedId);
  if (selected) return selected;
  const targetUrl = normalizeText(settings.server_url).replace(/\/+$/, "").toLowerCase();
  return (
    presets.find(
      (preset) => preset.base_url.replace(/\/+$/, "").toLowerCase() === targetUrl,
    ) || null
  );
};

export const serverTrustWarning = (settings = {}) => {
  const preset = selectedServerPreset(settings);
  const model = normalizeText(settings.transformer_model).toLowerCase();
  let serverHost = "";
  try {
    serverHost = new URL(normalizeText(settings.server_url)).hostname.toLowerCase();
  } catch {
    serverHost = "";
  }
  if (
    preset?.provider === "xai" ||
    serverHost === "api.x.ai" ||
    model.includes("grok")
  ) {
    return GROK_TRUST_WARNING;
  }
  return normalizeText(preset?.trust_warning);
};

export const makeCustomServerPreset = (sequence = Date.now()) => ({
  id: `custom-${sequence}`,
  name: "Custom endpoint",
  provider: "openai-compatible",
  base_url: "",
  api_key_env: "",
  description: "",
  builtin: false,
  api_key_set: false,
});
