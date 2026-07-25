import { isLocalRuntimeEntry } from "./modelUtils";
import {
  formatProviderLastOperation,
  isChatCapableProviderModelName,
} from "./providerRuntime";

export const RUNTIME_PANEL_LANES = Object.freeze({
  CLOUD_API: "cloud-api",
  SERVER_LAN: "server-lan",
  LOCAL_PROVIDER: "local-provider",
  DIRECT_LOCAL: "direct-local",
});

export const RUNTIME_AVAILABILITY = Object.freeze({
  USABLE: "usable",
  DEGRADED: "degraded",
  UNAVAILABLE: "unavailable",
  CHECKING: "checking",
});

const CHECKING_STATUSES = new Set([
  "checking",
  "connecting",
  "loading",
  "pending",
  "starting",
  "stopping",
  "updating",
]);
const DEGRADED_STATUSES = new Set(["degraded", "limited", "partial"]);
const UNAVAILABLE_STATUSES = new Set([
  "error",
  "failed",
  "missing",
  "offline",
  "unauthorized",
  "unavailable",
  "unconfigured",
  "unreachable",
]);
const USABLE_STATUSES = new Set([
  "bypassed",
  "connected",
  "live",
  "ok",
  "online",
  "ready",
  "usable",
]);

const cleanText = (value) => (typeof value === "string" ? value.trim() : "");

const cleanStatus = (value) => cleanText(value).toLowerCase();

const firstText = (...values) => {
  for (const value of values) {
    const cleaned = cleanText(value);
    if (cleaned) return cleaned;
  }
  return "";
};

const normalizeExplicitLane = (value) => {
  const normalized = cleanStatus(value).replace(/[\s_/]+/g, "-");
  if (["api", "cloud", "cloud-api"].includes(normalized)) {
    return RUNTIME_PANEL_LANES.CLOUD_API;
  }
  if (["server", "server-lan", "lan"].includes(normalized)) {
    return RUNTIME_PANEL_LANES.SERVER_LAN;
  }
  if (["provider", "local-provider"].includes(normalized)) {
    return RUNTIME_PANEL_LANES.LOCAL_PROVIDER;
  }
  if (["direct", "direct-local", "local-direct"].includes(normalized)) {
    return RUNTIME_PANEL_LANES.DIRECT_LOCAL;
  }
  return "";
};

const resolveLane = ({
  lane,
  mode,
  localRuntimeKind,
  localModel,
  transformerModel,
  providerRuntime,
}) => {
  const explicitLane = normalizeExplicitLane(lane);
  if (explicitLane) return explicitLane;

  const normalizedMode = cleanStatus(mode);
  if (normalizedMode === "server") return RUNTIME_PANEL_LANES.SERVER_LAN;
  if (normalizedMode === "api" || normalizedMode === "cloud") {
    return RUNTIME_PANEL_LANES.CLOUD_API;
  }

  if (normalizedMode === "local") {
    const normalizedKind = cleanStatus(localRuntimeKind);
    if (normalizedKind === "provider") return RUNTIME_PANEL_LANES.LOCAL_PROVIDER;
    if (normalizedKind === "direct") return RUNTIME_PANEL_LANES.DIRECT_LOCAL;
    if (
      isLocalRuntimeEntry(localModel) ||
      isLocalRuntimeEntry(transformerModel) ||
      (providerRuntime && typeof providerRuntime === "object")
    ) {
      return RUNTIME_PANEL_LANES.LOCAL_PROVIDER;
    }
    return RUNTIME_PANEL_LANES.DIRECT_LOCAL;
  }

  return RUNTIME_PANEL_LANES.CLOUD_API;
};

const normalizeLastOperation = (operation, now) => {
  if (!operation) return null;
  if (typeof operation === "string") {
    const label = operation.trim();
    return label ? { label, title: label, status: "done" } : null;
  }
  if (typeof operation !== "object") return null;
  if (cleanText(operation.label)) {
    return {
      label: cleanText(operation.label),
      title: cleanText(operation.title) || cleanText(operation.label),
      status: cleanStatus(operation.status) || "done",
    };
  }
  return formatProviderLastOperation(operation, now);
};

const statusIs = (statuses, value) => statuses.has(cleanStatus(value));

const resolveCloudContract = (input, now) => {
  const apiRuntime =
    input.apiRuntime && typeof input.apiRuntime === "object" ? input.apiRuntime : {};
  const apiStatus = cleanStatus(input.apiStatus || apiRuntime.status);
  const providerStatus = cleanStatus(
    input.apiProviderStatus || apiRuntime.provider_status,
  );
  const loading = Boolean(input.loading || input.apiLoading);
  const error = firstText(input.error, input.apiError, apiRuntime.error);

  let availability = RUNTIME_AVAILABILITY.CHECKING;
  if (
    error ||
    statusIs(UNAVAILABLE_STATUSES, apiStatus) ||
    statusIs(UNAVAILABLE_STATUSES, providerStatus)
  ) {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  } else if (
    loading ||
    statusIs(CHECKING_STATUSES, apiStatus) ||
    statusIs(CHECKING_STATUSES, providerStatus)
  ) {
    availability = RUNTIME_AVAILABILITY.CHECKING;
  } else if (
    statusIs(DEGRADED_STATUSES, apiStatus) ||
    statusIs(DEGRADED_STATUSES, providerStatus)
  ) {
    availability = RUNTIME_AVAILABILITY.DEGRADED;
  } else if (
    statusIs(USABLE_STATUSES, apiStatus) ||
    (!apiStatus && statusIs(USABLE_STATUSES, providerStatus))
  ) {
    availability = RUNTIME_AVAILABILITY.USABLE;
  }

  return {
    lane: RUNTIME_PANEL_LANES.CLOUD_API,
    model: firstText(input.apiModel, input.model, apiRuntime.model),
    endpoint: firstText(input.endpoint, input.apiEndpoint, apiRuntime.endpoint),
    availability,
    lastOperation: normalizeLastOperation(
      input.lastOperation || apiRuntime.last_operation,
      now,
    ),
  };
};

const resolveServerContract = (input, now) => {
  const serverRuntime =
    input.serverRuntime && typeof input.serverRuntime === "object"
      ? input.serverRuntime
      : {};
  const endpoint = firstText(
    input.endpoint,
    input.serverUrl,
    serverRuntime.base_url,
    serverRuntime.url,
  );
  const configuredModel = firstText(
    input.serverModel,
    input.transformerModel,
    input.model,
  );
  const loadedModel = firstText(
    input.serverLoadedModel,
    serverRuntime.loaded_model,
  );
  const model = firstText(loadedModel, configuredModel, serverRuntime.model);
  const models = (Array.isArray(input.serverModels)
      ? input.serverModels
      : Array.isArray(serverRuntime.models)
        ? serverRuntime.models
        : []
    )
    .map((entry) => cleanText(entry))
    .filter(Boolean);
  const configuredModelInInventory = Boolean(
    configuredModel &&
      models.some((entry) => cleanStatus(entry) === cleanStatus(configuredModel)),
  );
  const configuredModelMismatch = Boolean(
    configuredModel &&
      ((loadedModel && cleanStatus(loadedModel) !== cleanStatus(configuredModel)) ||
        (models.length > 0 && !configuredModelInInventory)),
  );
  const status = cleanStatus(input.serverStatus || serverRuntime.status);
  const loading = Boolean(input.loading || input.serverLoading);
  const error = firstText(input.error, input.serverError, serverRuntime.error);
  const reachable =
    serverRuntime.reachable === true ||
    statusIs(USABLE_STATUSES, status) ||
    models.length > 0 ||
    Boolean(cleanText(serverRuntime.loaded_model));

  let availability = RUNTIME_AVAILABILITY.CHECKING;
  if (!endpoint || error || statusIs(UNAVAILABLE_STATUSES, status)) {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  } else if (loading || statusIs(CHECKING_STATUSES, status)) {
    availability = RUNTIME_AVAILABILITY.CHECKING;
  } else if (reachable) {
    availability = configuredModelMismatch
      ? RUNTIME_AVAILABILITY.DEGRADED
      : loadedModel || configuredModelInInventory
        ? RUNTIME_AVAILABILITY.USABLE
        : RUNTIME_AVAILABILITY.DEGRADED;
  } else if (statusIs(DEGRADED_STATUSES, status)) {
    availability = RUNTIME_AVAILABILITY.DEGRADED;
  }

  return {
    lane: RUNTIME_PANEL_LANES.SERVER_LAN,
    model,
    endpoint,
    availability,
    lastOperation: normalizeLastOperation(
      input.lastOperation || serverRuntime.last_operation,
      now,
    ),
  };
};

const resolveProviderContract = (input, now) => {
  const providerRuntime =
    input.providerRuntime && typeof input.providerRuntime === "object"
      ? input.providerRuntime
      : {};
  const capabilities =
    providerRuntime.capabilities && typeof providerRuntime.capabilities === "object"
      ? providerRuntime.capabilities
      : {};
  const providerModels = Array.isArray(input.providerModels)
    ? input.providerModels.filter((model) => cleanText(model))
    : [];
  const providerMode = cleanStatus(input.providerMode || providerRuntime.mode);
  const externalEndpoint =
    capabilities.start_stop === false || providerMode === "remote-unmanaged";
  const endpoint = firstText(
    input.endpoint,
    input.localProviderBaseUrl,
    providerRuntime.base_url,
  );
  const model = firstText(
    providerRuntime.loaded_model,
    input.providerModel,
    input.providerPreferredModel,
    providerRuntime.effective_model_id,
    providerRuntime.effective_model,
    providerRuntime.preferred_model,
    !isLocalRuntimeEntry(input.model) ? input.model : "",
  );
  const status = cleanStatus(input.providerStatus || providerRuntime.status);
  const loading = Boolean(input.loading || input.providerLoading);
  const error = firstText(input.error, input.providerError);
  const endpointReachable =
    providerRuntime.server_running === true ||
    providerRuntime.reachable === true ||
    providerRuntime.inventory_reachable === true ||
    providerModels.length > 0;
  const namedChatModel = isChatCapableProviderModelName(model);
  const loadedModelName = cleanText(providerRuntime.loaded_model);
  const loadedChatModel =
    providerRuntime.chat_ready === true ||
    (providerRuntime.model_loaded === true &&
      (!loadedModelName || isChatCapableProviderModelName(loadedModelName)));

  let availability = RUNTIME_AVAILABILITY.CHECKING;
  if (
    error ||
    statusIs(UNAVAILABLE_STATUSES, status) ||
    (providerRuntime.chat_ready === false &&
      providerRuntime.inventory_reachable === false)
  ) {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  } else if (loading || statusIs(CHECKING_STATUSES, status)) {
    availability = RUNTIME_AVAILABILITY.CHECKING;
  } else if (externalEndpoint) {
    availability = !endpointReachable
      ? RUNTIME_AVAILABILITY.UNAVAILABLE
      : providerRuntime.model_state_stale === true
        ? RUNTIME_AVAILABILITY.DEGRADED
        : namedChatModel
          ? RUNTIME_AVAILABILITY.USABLE
          : RUNTIME_AVAILABILITY.DEGRADED;
  } else if (providerRuntime.model_state_stale === true && loadedChatModel) {
    availability = RUNTIME_AVAILABILITY.DEGRADED;
  } else if (loadedChatModel) {
    availability = RUNTIME_AVAILABILITY.USABLE;
  } else if (providerRuntime.server_running === true) {
    availability = RUNTIME_AVAILABILITY.DEGRADED;
  } else {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  }

  return {
    lane: RUNTIME_PANEL_LANES.LOCAL_PROVIDER,
    model,
    endpoint,
    availability,
    lastOperation: normalizeLastOperation(
      input.lastOperation || providerRuntime.last_operation,
      now,
    ),
  };
};

const resolveDirectLocalContract = (input, now) => {
  const runtime = input.runtime && typeof input.runtime === "object" ? input.runtime : {};
  const loadState = cleanStatus(runtime.load_state || runtime.status);
  const model = firstText(
    runtime.effective_model_id,
    runtime.model,
    input.localModel,
    input.transformerModel,
    input.model,
  );
  const preflight =
    runtime.preflight && typeof runtime.preflight === "object" ? runtime.preflight : {};
  const loading = Boolean(input.loading || input.localLoading);
  const error = firstText(input.error, input.localError, runtime.load_error, runtime.error);

  let availability = RUNTIME_AVAILABILITY.CHECKING;
  if (
    error ||
    preflight.ready === false ||
    statusIs(UNAVAILABLE_STATUSES, loadState)
  ) {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  } else if (loading || statusIs(CHECKING_STATUSES, loadState)) {
    availability = RUNTIME_AVAILABILITY.CHECKING;
  } else if (
    runtime.loaded === true ||
    ["loaded", "live", "ready"].includes(loadState)
  ) {
    availability = RUNTIME_AVAILABILITY.USABLE;
  } else if (model && Object.keys(runtime).length > 0) {
    availability = RUNTIME_AVAILABILITY.DEGRADED;
  } else if (!model) {
    availability = RUNTIME_AVAILABILITY.UNAVAILABLE;
  }

  return {
    lane: RUNTIME_PANEL_LANES.DIRECT_LOCAL,
    model,
    endpoint: "",
    availability,
    lastOperation: normalizeLastOperation(
      input.lastOperation || runtime.last_operation,
      now,
    ),
  };
};

/**
 * Collapse runtime-specific snapshots into the five fields shared by every
 * runtime panel. `mode: "server"` always remains Server/LAN, even if its URL
 * points at LM Studio or another provider also supported by the local lane.
 */
export const resolveRuntimePanelContract = (input = {}, now = Date.now()) => {
  const lane = resolveLane(input);
  if (lane === RUNTIME_PANEL_LANES.SERVER_LAN) {
    return resolveServerContract(input, now);
  }
  if (lane === RUNTIME_PANEL_LANES.LOCAL_PROVIDER) {
    return resolveProviderContract(input, now);
  }
  if (lane === RUNTIME_PANEL_LANES.DIRECT_LOCAL) {
    return resolveDirectLocalContract(input, now);
  }
  return resolveCloudContract(input, now);
};
