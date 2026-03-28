import React, { useContext, useEffect, useMemo, useState } from "react";
import AppBar from "@mui/material/AppBar";
import Toolbar from "@mui/material/Toolbar";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import { Link, useLocation } from "react-router-dom";
import axios from "axios";
import { GlobalContext } from "../main";
import {
  buildModelGroups,
  DEFAULT_API_MODELS,
  formatLocalRuntimeLabel,
  isLocalRuntimeEntry,
  LOCAL_RUNTIME_ENTRIES,
  normalizeModelId,
  SUGGESTED_LOCAL_MODELS,
} from "../utils/modelUtils";
import "../styles/TopBar.css";

const suggestedLangModels = SUGGESTED_LOCAL_MODELS;
const mobileTopbarQuery =
  "(max-width: 600px), (orientation: portrait) and (max-width: 900px)";
const EMPTY_GLOBAL_STATE = Object.freeze({});
const NOOP_SET_STATE = () => {};

const fireAndForget = (request) => {
  if (request && typeof request.catch === "function") {
    request.catch(() => {});
  }
};

const formatRelativeTime = (timestamp) => {
  if (timestamp == null) return null;
  const diff = Date.now() - timestamp;
  if (!Number.isFinite(diff)) return null;
  if (diff < 0) return "in future";
  const seconds = Math.floor(diff / 1000);
  if (seconds <= 1) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
};

const formatClockTime = (timestamp) => {
  if (timestamp == null) return null;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const generateDefaultSessionName = (timestamp = Date.now()) => {
  const date = new Date(timestamp);
  const pad = (n) => String(n).padStart(2, "0");
  return `New Chat ${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const TopBar = () => {
  const globalContext = useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const location = useLocation();
  const currentTab = location.pathname.startsWith("/settings")
    ? "/settings"
    : location.pathname.startsWith("/knowledge")
      ? "/knowledge"
      : "/";

  const [serverStatus, setServerStatus] = useState("offline"); // offline | loading | online
  const [localStatus, setLocalStatus] = useState("offline"); // offline | loading | online | degraded
  const [isMobileTopbar, setIsMobileTopbar] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return false;
    }
    return window.matchMedia(mobileTopbarQuery).matches;
  });
  const [isMobileControlsOpen, setIsMobileControlsOpen] = useState(false);

  const apiModelsAvailable = Array.isArray(state.apiModels) ? state.apiModels : [];
  const apiModelsAvailableSet = useMemo(
    () => new Set(apiModelsAvailable),
    [apiModelsAvailable],
  );
  const apiModelGroups = useMemo(
    () =>
      buildModelGroups({
        defaults: DEFAULT_API_MODELS,
        discovered: apiModelsAvailable,
        current: state.apiModel,
      }),
    [apiModelsAvailable, state.apiModel],
  );
  const registeredTransformerAliases = useMemo(() => {
    const entries = Array.isArray(state.registeredLocalModels)
      ? state.registeredLocalModels
      : [];
    const aliases = entries
      .map((entry) => {
        const alias = typeof entry?.alias === "string" ? entry.alias.trim() : "";
        if (!alias) return null;
        if (entry?.exists === false) return null;
        const modelType = String(entry?.model_type || "other").toLowerCase();
        if (modelType !== "transformer" && modelType !== "other") return null;
        return alias;
      })
      .filter(Boolean);
    return Array.from(new Set(aliases));
  }, [state.registeredLocalModels]);
  const localModelOptions = useMemo(() => {
    const base = [
      ...registeredTransformerAliases,
      ...(Array.isArray(suggestedLangModels) ? suggestedLangModels : []),
      ...(Array.isArray(LOCAL_RUNTIME_ENTRIES) ? LOCAL_RUNTIME_ENTRIES : []),
    ];
    const deduped = Array.from(new Set(base));
    const current = state.localModel;
    if (current && !deduped.includes(current)) {
      return [current, ...deduped];
    }
    return deduped;
  }, [state.localModel, registeredTransformerAliases]);
  const serverModelOptions = useMemo(() => {
    const base = [
      ...registeredTransformerAliases,
      ...(Array.isArray(suggestedLangModels) ? suggestedLangModels : []),
    ];
    const deduped = Array.from(new Set(base));
    const current = state.transformerModel;
    if (current && !deduped.includes(current)) {
      return [current, ...deduped];
    }
    return deduped;
  }, [state.transformerModel, registeredTransformerAliases]);

  const setBackendMode = (mode) => {
    setState((prev) => ({ ...prev, backendMode: mode }));
  };

  const modeDisplayLabel = (mode) => {
    if (mode === "api") return "cloud";
    if (mode === "local") return "local";
    if (mode === "server") return "server";
    return mode || "unknown";
  };

  // Cycle backend mode through a predefined order (Cloud API -> Local -> Server/LAN)
  const toggleBackendMode = () => {
    const order = ["api", "local", "server"];
    const idx = order.indexOf(state.backendMode);
    const next = order[(idx + 1) % order.length];
    setBackendMode(next);
  };

  // Cycle approval mode through one-word labels
  const toggleApprovalMode = () => {
    const order = ["all", "high", "auto"];
    const idx = order.indexOf(state.approvalLevel);
    const next = order[(idx + 1) % order.length];
    setState((prev) => ({ ...prev, approvalLevel: next }));
  };

  const toggleTheme = () => {
    setState((prev) => ({
      ...prev,
      theme: prev.theme === "dark" ? "light" : "dark",
    }));
  };

  const renderThemeToggle = () => (
    <button
      className="topbar-theme-toggle"
      onClick={(e) => {
        const btn = e.currentTarget;
        // Cancel any pending hover-triggered toggle
        if (btn.__hoverTimer) {
          clearTimeout(btn.__hoverTimer);
          btn.__hoverTimer = null;
        }
        // If a hover just triggered, suppress immediate click bounce-back
        if (btn.__lastHoverToggleAt && Date.now() - btn.__lastHoverToggleAt < 600) {
          return;
        }
        toggleTheme();
      }}
      aria-label="Toggle theme"
      onMouseEnter={(e) => {
        const btn = e.currentTarget;
        if (btn.__hoverTimer) clearTimeout(btn.__hoverTimer);
        // Debounce hover: apply after 600ms if still hovering
        btn.__hoverTimer = setTimeout(() => {
          btn.__lastHoverToggleAt = Date.now();
          toggleTheme();
        }, 600);
      }}
      onMouseLeave={(e) => {
        const btn = e.currentTarget;
        if (btn.__hoverTimer) {
          clearTimeout(btn.__hoverTimer);
          btn.__hoverTimer = null;
        }
      }}
    >
      {state.theme === "dark" ? (
        // Moon icon (show current state)
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
        </svg>
      ) : (
        // Sun icon (show current state)
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.8 1.42-1.42zm10.45 12.02l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM12 4V1h-0v3h0zm0 19v-3h0v3h0zM4 12H1v0h3v0zm19 0h-3v0h3v0zM6.76 19.16l-1.42 1.42-1.79-1.8 1.41-1.41 1.8 1.79zM17.24 4.84l1.4-1.4 1.8 1.79-1.41 1.41-1.79-1.8zM12 7a5 5 0 100 10 5 5 0 000-10z" />
        </svg>
      )}
    </button>
  );

  const renderPrimaryControls = () => (
    <>
      <Link
        to="/settings"
        className="status-indicator"
        title={statusIndicatorTitle}
        aria-label="Backend status"
      >
        <span className={`status-dot ${statusDotClass}`} aria-hidden="true" />
      </Link>
      <Link
        to="/settings"
        className="status-indicator"
        title={wsStatusTitle}
        aria-label={wsStatusTitle}
      >
        <span
          className={`status-dot ${state.wsStatus === "online" ? "ok" : "err"}`}
          aria-hidden="true"
        />
      </Link>
      <button
        type="button"
        className="chip backend-chip"
        onClick={toggleBackendMode}
        aria-label="Backend mode"
        title="Backend mode: click to cycle Cloud API -> Local (on-device) -> Server/LAN"
      >
        {modeDisplayLabel(state.backendMode)}
      </button>
      {state.backendMode === "server" ? (
        <input
          className="server-ip-input"
          type="text"
          inputMode="url"
          placeholder="server/lan url"
          value={state.serverUrl || ""}
          onChange={(e) => setState((prev) => ({ ...prev, serverUrl: e.target.value }))}
          onBlur={() => {
            const value = (state.serverUrl || "").trim();
            axios.post("/api/settings", { server_url: value }).catch(() => {});
          }}
          title="Server/LAN URL"
        />
      ) : (
        <select
          className="model-select"
          value={
            state.backendMode === "api"
              ? state.apiModel
              : state.backendMode === "server"
                ? state.transformerModel
                : state.localModel
          }
          onChange={handleModelChange}
          title="Select model"
        >
          {state.backendMode === "api" ? (
            <>
              <optgroup label="defaults">
                {apiModelGroups.defaults.map((m) => {
                  const disabled =
                    apiModelsAvailableSet.size > 0 && !apiModelsAvailableSet.has(m);
                  const label = disabled ? `${m} (unavailable)` : m;
                  return (
                    <option key={m} value={m} disabled={disabled}>
                      {label}
                    </option>
                  );
                })}
              </optgroup>
              {apiModelGroups.extras.length > 0 && (
                <optgroup
                  label={`available${apiModelsAvailable.length ? ` (${apiModelsAvailable.length})` : ""}`}
                >
                  {apiModelGroups.extras.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </optgroup>
              )}
            </>
          ) : (
            (state.backendMode === "server" ? serverModelOptions : localModelOptions).map(
              (m) => (
                <option key={m} value={m}>
                  {state.backendMode === "local" && isLocalRuntimeEntry(m)
                    ? formatLocalRuntimeLabel(m)
                    : m}
                </option>
              ),
            )
          )}
        </select>
      )}
      <button
        type="button"
        className="chip approval-chip"
        onClick={toggleApprovalMode}
        aria-label="Approval mode"
        title="Approval level: click to cycle all -> high -> auto"
      >
        {state.approvalLevel}
      </button>
    </>
  );

  const startNewChatSession = () => {
    const timestamp = Date.now();
    const newId = `sess-${timestamp}`;
    setState((prev) => ({
      ...prev,
      conversation: [],
      sessionId: newId,
      sessionName: generateDefaultSessionName(timestamp),
    }));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("float:new-chat"));
    }
  };

  const handleModelChange = (e) => {
    const value = e.target.value;
    if (state.backendMode === "api") {
      setState((prev) => ({ ...prev, apiModel: value }));
      // Persist so Settings + background jobs remain consistent.
      fireAndForget(axios.post("/api/settings", { openai_model: value }));
    } else if (state.backendMode === "local") {
      setState((prev) => ({ ...prev, localModel: value }));
      // Persist local transformers model to backend so LLMService can load it
      const payload = { transformer_model: value };
      if (isLocalRuntimeEntry(value)) {
        payload.local_provider = normalizeModelId(value);
      }
      fireAndForget(axios.post("/api/settings", payload));
    } else {
      // server mode uses transformerModel catalog
      setState((prev) => ({ ...prev, transformerModel: value }));
      // For server mode, this is also passed per-request
    }
  };

  // derive a status color for the status dot
  const normalizedApiStatus = useMemo(() => {
    if (state.backendMode !== "api") return state.apiStatus || "loading";
    const provider = state.apiProviderStatus;
    if (!provider || ["online", "bypassed", "unknown"].includes(provider)) {
      return state.apiStatus || "loading";
    }
    if (state.apiStatus === "offline") return "offline";
    return "degraded";
  }, [state.backendMode, state.apiStatus, state.apiProviderStatus]);

  const statusForMode = useMemo(() => {
    if (state.backendMode === "api") return normalizedApiStatus;
    if (state.backendMode === "server") return serverStatus;
    return localStatus;
  }, [state.backendMode, normalizedApiStatus, serverStatus, localStatus]);

  const displayDeviceName = useMemo(() => {
    const list = Array.isArray(state.devices) ? state.devices : [];
    if (state.inferenceDevice) {
      const match = list.find(
        (device) =>
          device &&
          (device.id === state.inferenceDevice ||
            device.name === state.inferenceDevice),
      );
      if (match) {
        return match.name || match.id || "";
      }
    }
    if (state.defaultDevice && typeof state.defaultDevice === "object") {
      return state.defaultDevice.name || state.defaultDevice.id || "";
    }
    return "";
  }, [state.devices, state.inferenceDevice, state.defaultDevice]);

  const friendlyLocalStatus = localStatus === "online" ? "ready" : localStatus;

  const providerNote = useMemo(() => {
    const value = (state.apiProviderStatus || "").toLowerCase();
    if (!value || ["online", "bypassed", "unknown"].includes(value)) return "";
    if (value === "unconfigured") return "provider missing key";
    if (value === "unauthorized") return "provider unauthorized";
    if (value === "unreachable") return "provider unreachable";
    if (value === "offline") return "provider offline";
    if (value === "error") return "provider error";
    return `provider ${value}`;
  }, [state.apiProviderStatus]);

  const statusIndicatorTitle = useMemo(() => {
    if (state.backendMode === "api") {
      const extra = providerNote ? ` (${providerNote})` : "";
      return `Cloud API: ${normalizedApiStatus}${extra}`;
    }
    if (state.backendMode === "server") {
      return `Server/LAN: ${serverStatus}`;
    }
    const suffix = displayDeviceName ? ` (${displayDeviceName})` : "";
    return `Local (on-device): ${friendlyLocalStatus}${suffix}`;
  }, [
    state.backendMode,
    normalizedApiStatus,
    providerNote,
    serverStatus,
    friendlyLocalStatus,
    displayDeviceName,
  ]);

  const wsStatusTitle = useMemo(() => {
    const parts = [`WS: ${state.wsStatus}`];
    const lastEventAgo = formatRelativeTime(state.wsLastEventAt);
    const lastEventClock = formatClockTime(state.wsLastEventAt);
    if (lastEventAgo) {
      parts.push(
        `last event ${lastEventClock ? `${lastEventAgo} (${lastEventClock})` : lastEventAgo}`,
      );
    }
    if (state.wsLastError) {
      const errAgo = formatRelativeTime(state.wsLastErrorAt);
      const errClock = formatClockTime(state.wsLastErrorAt);
      const when =
        errAgo && errClock
          ? `${errAgo} (${errClock})`
          : errAgo || errClock || "";
      const truncatedError =
        state.wsLastError.length > 160
          ? `${state.wsLastError.slice(0, 157)}...`
          : state.wsLastError;
      parts.push(`error${when ? ` ${when}` : ""}: ${truncatedError}`);
    }
    return parts.join(" \u2022 ");
  }, [state.wsStatus, state.wsLastEventAt, state.wsLastError, state.wsLastErrorAt]);

  const statusDotClass =
    statusForMode === "online"
      ? "ok"
      : statusForMode === "loading" || statusForMode === "degraded"
      ? "warn"
      : "err";

  useEffect(() => {
    let canceled = false;
    const shouldFetchDevices =
      !(Array.isArray(state.devices) && state.devices.length > 0) ||
      !state.defaultDevice;
    if (!shouldFetchDevices) {
      return undefined;
    }
    const fetchDevices = async () => {
      try {
        const resp = await axios.get("/api/settings");
        if (canceled) return;
        const data = resp.data || {};
        const devices = Array.isArray(data.devices) ? data.devices : null;
        const defaultDevice = data.default_device || null;
        if (!devices && !defaultDevice) return;
        setState((prev) => ({
          ...prev,
          devices: devices || prev.devices || [],
          defaultDevice: defaultDevice || prev.defaultDevice || null,
          inferenceDevice:
            data.inference_device ||
            prev.inferenceDevice ||
            (defaultDevice
              ? defaultDevice.id || defaultDevice.name
              : prev.inferenceDevice),
        }));
      } catch {
        // Ignore device detection failures; tooltip will omit device name.
      }
    };
    fetchDevices();
    return () => {
      canceled = true;
    };
  }, [state.devices, state.defaultDevice, setState]);

  // Load registered aliases once so Local/Server dropdowns include custom entries.
  useEffect(() => {
    let canceled = false;
    const fetchRegistered = async () => {
      try {
        const response = await axios.get("/api/models/registered");
        if (canceled) return;
        const entries = Array.isArray(response?.data?.models)
          ? response.data.models
          : [];
        setState((prev) => ({ ...prev, registeredLocalModels: entries }));
      } catch {
        // Ignore fetch failures; TopBar will keep the last known options.
      }
    };
    fetchRegistered();
    return () => {
      canceled = true;
    };
  }, [setState]);

  // Ping server health when in server mode or when URL changes
  useEffect(() => {
    let aborted = false;
    const check = async () => {
      if (!state.serverUrl) {
        setServerStatus("offline");
        return;
      }
      try {
        setServerStatus("loading");
        const url = new URL(state.serverUrl, window.location.href);
        const base = url.href.replace(/\/$/, "");
        const origin = `${url.protocol}//${url.host}`;
        const healthTargets = Array.from(new Set([`${base}/health`, `${origin}/health`]));
        let healthOk = false;
        for (const target of healthTargets) {
          const res = await fetch(target, { method: "GET" }).catch(() => null);
          if (res && res.ok) {
            healthOk = true;
            break;
          }
        }
        if (!healthOk) {
          const reachTargets = Array.from(new Set([base, origin]));
          let reachable = false;
          for (const target of reachTargets) {
            const head = await fetch(target, { method: "HEAD" }).catch(() => null);
            if (head && head.ok) {
              reachable = true;
              break;
            }
          }
          if (aborted) return;
          setServerStatus(reachable ? "online" : "offline");
        } else {
          if (aborted) return;
          setServerStatus("online");
        }
      } catch {
        if (!aborted) setServerStatus("offline");
      }
    };
    if (state.backendMode === "server") {
      check();
    }
    return () => {
      aborted = true;
    };
  }, [state.backendMode, state.serverUrl]);

  // Resolve local model readiness by checking backend for model presence
  useEffect(() => {
    let canceled = false;
    const checkLocal = async () => {
      const modelName = state.localModel;
      if (!modelName) {
        setLocalStatus("offline");
        return;
      }
      try {
        setLocalStatus("loading");
        if (isLocalRuntimeEntry(modelName)) {
          const providerResp = await axios.get("/api/llm/provider/status", {
            params: { provider: normalizeModelId(modelName) },
          });
          if (canceled) return;
          const runtime = providerResp?.data?.runtime || {};
          if (runtime.model_loaded) {
            setLocalStatus("online");
          } else if (runtime.server_running || runtime.installed) {
            setLocalStatus("degraded");
          } else {
            setLocalStatus("offline");
          }
          return;
        }
        const resp = await axios.get(
          `/api/models/verify/${encodeURIComponent(modelName)}`,
        );
        if (canceled) return;
        const data = resp.data || {};
        if (data.verified) {
          setLocalStatus("online");
        } else if (data.exists) {
          setLocalStatus("degraded");
        } else {
          setLocalStatus("offline");
        }
      } catch {
        if (!canceled) setLocalStatus("offline");
      }
    };
    if (state.backendMode === "local") {
      checkLocal();
    } else {
      setLocalStatus("offline");
    }
    return () => {
      canceled = true;
    };
  }, [state.backendMode, state.localModel]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return undefined;
    }
    const mediaQuery = window.matchMedia(mobileTopbarQuery);
    const updateMobileState = () => setIsMobileTopbar(mediaQuery.matches);
    updateMobileState();
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", updateMobileState);
      return () => mediaQuery.removeEventListener("change", updateMobileState);
    }
    mediaQuery.addListener(updateMobileState);
    return () => mediaQuery.removeListener(updateMobileState);
  }, []);

  useEffect(() => {
    if (!isMobileTopbar) {
      setIsMobileControlsOpen(false);
    }
  }, [isMobileTopbar]);

  useEffect(() => {
    setIsMobileControlsOpen(false);
  }, [location.pathname]);

  return (
    <AppBar
      position="fixed"
      color="transparent"
      elevation={0}
      className="topbar-appbar"
      sx={{ zIndex: 2000 }}
    >
      <Toolbar className="topbar-toolbar">
        <div className="topbar-inner topbar-content center-rail">
          <div className="topbar-left">
            <h1 className="topbar-title">
              <Link to="/" className="topbar-link">
                float
              </Link>
            </h1>
            <span className="session-name" title={state.sessionName}>{state.sessionName}</span>
          </div>
          <Tabs
            value={currentTab}
            textColor="inherit"
            className="topbar-tabs"
            variant="scrollable"
            scrollButtons="auto"
            allowScrollButtonsMobile
          >
            <Tab
              label="chat"
              value="/"
              component={Link}
              to="/"
              disableRipple
              onDoubleClick={startNewChatSession}
            />
            <Tab
              label="knowledge"
              value="/knowledge"
              component={Link}
              to="/knowledge"
              disableRipple
            />
            <Tab
              label="settings"
              value="/settings"
              component={Link}
              to="/settings"
              disableRipple
            />
          </Tabs>
          <div className="topbar-actions">
            {isMobileTopbar ? (
              <>
                <button
                  type="button"
                  className={`topbar-theme-toggle topbar-mobile-menu-toggle${isMobileControlsOpen ? " open" : ""}`}
                  onClick={() => setIsMobileControlsOpen((prev) => !prev)}
                  aria-label="Toggle inference controls"
                  aria-expanded={isMobileControlsOpen}
                  aria-controls="topbar-mobile-controls"
                  title="Toggle inference controls"
                >
                  <span className="topbar-mobile-menu-icon" aria-hidden="true">&#9662;</span>
                </button>
                {renderThemeToggle()}
              </>
            ) : (
              <>
                {renderPrimaryControls()}
                {renderThemeToggle()}
              </>
            )}
          </div>
        </div>
      </Toolbar>
      {isMobileTopbar && isMobileControlsOpen ? (
        <div className="topbar-mobile-controls-wrap">
          <div
            id="topbar-mobile-controls"
            className="topbar-mobile-controls-popover"
          >
            <div className="topbar-mobile-controls-row">{renderPrimaryControls()}</div>
          </div>
        </div>
      ) : null}
    </AppBar>
  );
};

export default TopBar;
