import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import "../styles/ProgressBar.css";

const DEFAULT_EXPORT_DEFAULTS = {
  format: "md",
  includeChat: true,
  includeThoughts: true,
  includeTools: true,
};

const DEVICE_SCOPE_OPTIONS = ["sync", "stream", "files"];
const SYNC_ACTION_NAMES = new Set(["sync_pull", "sync_ingest"]);
const SYNC_PROGRESS_PRESETS = {
  preview: {
    title: "Previewing sync",
    tone: "preview",
    phases: [
      { label: "Checking the remote device", progress: 0.18, delayMs: 0 },
      { label: "Comparing selected sections", progress: 0.46, delayMs: 900 },
      { label: "Building the preview card", progress: 0.74, delayMs: 2400 },
      { label: "Waiting for the final response", progress: 0.9, delayMs: 5200 },
    ],
  },
  pull: {
    title: "Pulling from remote",
    tone: "pull",
    phases: [
      { label: "Preparing the pull request", progress: 0.16, delayMs: 0 },
      { label: "Reading remote data", progress: 0.42, delayMs: 800 },
      { label: "Applying changes locally", progress: 0.7, delayMs: 2600 },
      { label: "Refreshing synced indexes", progress: 0.9, delayMs: 5600 },
    ],
  },
  push: {
    title: "Pushing to remote",
    tone: "push",
    phases: [
      { label: "Preparing the push request", progress: 0.16, delayMs: 0 },
      { label: "Packaging selected sections", progress: 0.42, delayMs: 800 },
      { label: "Sending data to the remote", progress: 0.7, delayMs: 2600 },
      { label: "Waiting for the remote to finish", progress: 0.9, delayMs: 5600 },
    ],
  },
};

const normalizeExportFormat = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "markdown") return "md";
  if (raw === "txt") return "text";
  return raw === "json" || raw === "text" || raw === "md" ? raw : "md";
};

const normalizePeerScopes = (value) => {
  if (!Array.isArray(value)) return ["sync"];
  const seen = new Set();
  const scopes = value
    .map((item) => String(item || "").trim().toLowerCase())
    .filter((scope) => DEVICE_SCOPE_OPTIONS.includes(scope) && !seen.has(scope) && seen.add(scope));
  return scopes.length ? scopes : ["sync"];
};

const normalizeWorkspaceIdList = (value) => {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value
    .map((item) => String(item || "").trim())
    .filter((workspaceId) => workspaceId && !seen.has(workspaceId) && seen.add(workspaceId));
};

const cleanWorkspaceNamespace = (value) =>
  String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .map((segment) => segment.trim())
    .filter((segment) => segment && segment !== "." && segment !== "..")
    .join("/");

const slugifyWorkspaceToken = (value, fallback = "workspace") => {
  const slug = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
};

const buildWorkspaceId = () =>
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `workspace-${crypto.randomUUID()}`
    : `workspace-${Date.now()}`;

const coerceWorkspaceProfiles = (value) => {
  const seen = new Set(["root"]);
  const root = {
    id: "root",
    name: "Main workspace",
    slug: "main",
    namespace: "",
    root_path: "data/files/workspace",
    kind: "root",
    imported: false,
    is_root: true,
    source_peer_id: "",
    source_device_name: "",
    source_workspace_id: "",
    source_workspace_name: "",
  };
  const profiles = [root];
  if (!Array.isArray(value)) return profiles;
  value.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") return;
    const id =
      typeof entry.id === "string" && entry.id.trim()
        ? entry.id.trim()
        : `workspace-${index + 1}`;
    if (!id || seen.has(id) || id === "root") return;
    seen.add(id);
    profiles.push({
      id,
      name:
        typeof entry.name === "string" && entry.name.trim()
          ? entry.name.trim()
          : `Workspace ${index + 1}`,
      slug:
        typeof entry.slug === "string" && entry.slug.trim()
          ? entry.slug.trim()
          : slugifyWorkspaceToken(entry.name || id),
      namespace: cleanWorkspaceNamespace(entry.namespace),
      root_path:
        typeof entry.root_path === "string" && entry.root_path.trim()
          ? entry.root_path.trim()
          : `data/files/workspace/${slugifyWorkspaceToken(entry.name || id)}`,
      kind:
        typeof entry.kind === "string" && entry.kind.trim()
          ? entry.kind.trim()
          : "local",
      imported: entry.imported === true,
      is_root: false,
      source_peer_id:
        typeof entry.source_peer_id === "string" ? entry.source_peer_id.trim() : "",
      source_device_name:
        typeof entry.source_device_name === "string"
          ? entry.source_device_name.trim()
          : "",
      source_workspace_id:
        typeof entry.source_workspace_id === "string"
          ? entry.source_workspace_id.trim()
          : "",
      source_workspace_name:
        typeof entry.source_workspace_name === "string"
          ? entry.source_workspace_name.trim()
          : "",
    });
  });
  return profiles;
};

const workspaceById = (profiles, workspaceId) =>
  coerceWorkspaceProfiles(profiles).find((profile) => profile.id === workspaceId) || null;

const workspaceLabel = (profiles, workspaceId) =>
  workspaceById(profiles, workspaceId)?.name || workspaceId || "workspace";

const coerceSavedPeers = (value) =>
  Array.isArray(value)
    ? value
        .filter((entry) => entry && typeof entry === "object")
        .map((entry, index) => ({
          id:
            typeof entry.id === "string" && entry.id.trim()
              ? entry.id.trim()
              : `peer-${index + 1}`,
          label:
            typeof entry.label === "string" && entry.label.trim()
              ? entry.label.trim()
              : "Unnamed device",
          remote_url: typeof entry.remote_url === "string" ? entry.remote_url.trim() : "",
          scopes: normalizePeerScopes(entry.scopes),
          remote_device_id:
            typeof entry.remote_device_id === "string" ? entry.remote_device_id.trim() : "",
          public_key: typeof entry.public_key === "string" ? entry.public_key.trim() : "",
          remote_device_name:
            typeof entry.remote_device_name === "string" ? entry.remote_device_name.trim() : "",
          last_used_at:
            typeof entry.last_used_at === "string" ? entry.last_used_at.trim() : "",
          remote_public_key:
            typeof entry.remote_public_key === "string" ? entry.remote_public_key.trim() : "",
          local_workspace_ids: normalizeWorkspaceIdList(entry.local_workspace_ids),
          remote_workspace_ids: normalizeWorkspaceIdList(entry.remote_workspace_ids),
          workspace_mode:
            String(entry.workspace_mode || "").trim().toLowerCase() === "import"
              ? "import"
              : "merge",
          local_target_workspace_id:
            typeof entry.local_target_workspace_id === "string"
              ? entry.local_target_workspace_id.trim()
              : "root",
          remote_target_workspace_id:
            typeof entry.remote_target_workspace_id === "string"
              ? entry.remote_target_workspace_id.trim()
              : "root",
        }))
        .filter((entry) => entry.remote_url)
    : [];

const extractSyncError = (error, fallback) =>
  error?.response?.data?.detail || error?.message || fallback;

const isSyncRequestCancelled = (error, controller) => {
  const abortedReason = controller?.signal?.aborted ? controller.signal.reason : null;
  if (abortedReason === "user_cancelled" || abortedReason === "component_unmounted") {
    return true;
  }
  return error?.code === "ERR_CANCELED" || error?.name === "CanceledError";
};

const formatDateTime = (value) => {
  if (!value) return "never";
  const numeric = Number(value);
  const date = Number.isFinite(numeric) && numeric > 0 ? new Date(numeric * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString();
};

const syncPreviewStatusLabel = (status) => {
  const key = String(status || "").trim().toLowerCase();
  if (key === "only_remote") return "Only remote";
  if (key === "only_local") return "Only local";
  if (key === "remote_newer") return "Remote newer";
  if (key === "local_newer") return "Local newer";
  if (key === "identical") return "Identical";
  return key || "Changed";
};

const SYNC_ACTIONABLE_STATUSES = {
  pull: new Set(["only_remote", "remote_newer"]),
  push: new Set(["only_local", "local_newer"]),
};

const normalizeSyncSelectionIds = (value) => {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value
    .map((item) => String(item || "").trim())
    .filter((item) => item && !seen.has(item) && seen.add(item));
};

const syncSectionDiffItems = (section) =>
  Array.isArray(section?.all_items)
    ? section.all_items
    : Array.isArray(section?.items)
      ? section.items
      : [];

const syncSectionActionableItems = (section, direction) =>
  syncSectionDiffItems(section).filter((item) =>
    SYNC_ACTIONABLE_STATUSES[direction]?.has(String(item?.status || "").trim().toLowerCase()),
  );

const syncSectionSelectedCount = (selections, direction, sectionKey) =>
  normalizeSyncSelectionIds(selections?.[direction]?.[sectionKey]).length;

const describeSyncDirectionSummary = (section, direction, remoteLabel) => {
  const label = remoteLabel || "remote";
  const identical = Number(section?.identical || 0);
  if (direction === "pull") {
    const onlyRemote = Number(section?.only_remote || 0);
    const remoteNewer = Number(section?.remote_newer || 0);
    if (!onlyRemote && !remoteNewer) {
      return identical
        ? `${identical} already match here. Nothing new to pull from ${label}.`
        : `Nothing to pull from ${label}.`;
    }
    return `${onlyRemote} new on ${label}, ${remoteNewer} newer on ${label}, ${identical} already match here.`;
  }
  const onlyLocal = Number(section?.only_local || 0);
  const localNewer = Number(section?.local_newer || 0);
  if (!onlyLocal && !localNewer) {
    return identical
      ? `${identical} already match there. Nothing to push from this device.`
      : "Nothing to push from this device.";
  }
  return `${onlyLocal} only on this device, ${localNewer} newer here, ${identical} already match there.`;
};

const describeSyncItemTiming = (item) => {
  const localLabel = String(item?.local_updated_at_label || "").trim();
  const remoteLabel = String(item?.remote_updated_at_label || "").trim();
  if (localLabel && remoteLabel) return `Here ${localLabel} | There ${remoteLabel}`;
  if (remoteLabel) return `There ${remoteLabel}`;
  if (localLabel) return `Here ${localLabel}`;
  return "";
};

const summarizeSyncSections = (sectionMap) =>
  Object.entries(sectionMap || {})
    .filter(([, section]) => section)
    .map(([, section]) => {
      const applied = Number(section?.applied || 0);
      const skipped = Number(section?.skipped || 0);
      return `${section?.label || "section"}: ${applied} applied, ${skipped} skipped`;
    })
    .join(" | ") || "No changes were applied.";

const inferImportFormatFromFilename = (name) => {
  const value = String(name || "").trim().toLowerCase();
  if (value.endsWith(".zip")) return "zip";
  if (value.endsWith(".json")) return "json";
  if (value.endsWith(".md")) return "markdown";
  if (value.endsWith(".txt")) return "text";
  return "auto";
};

const buildPeerId = () =>
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `peer-${Date.now()}`;

const summarizeRequestedSections = (sections) =>
  Array.isArray(sections) && sections.length ? sections.join(", ") : "all sections";

const describePeerStatus = (peer, options = {}) => {
  if (peer?.remote_device_id && options?.reachable) {
    return { key: "connected", label: "connected" };
  }
  if (peer?.remote_device_id) {
    return { key: "paired", label: "paired" };
  }
  return { key: "saved", label: "saved target" };
};

const coerceSyncActions = (value) =>
  Array.isArray(value)
    ? value
        .filter((entry) => entry && typeof entry === "object")
        .filter(
          (entry) =>
            String(entry.kind || "").trim() === "sync"
            && SYNC_ACTION_NAMES.has(String(entry.name || "").trim()),
        )
        .sort(
          (left, right) =>
            Number(right?.created_at_ts || right?.timestamp || 0)
            - Number(left?.created_at_ts || left?.timestamp || 0),
        )
    : [];

const describeSyncHistoryStatus = (action) => {
  if (action?.reverted_at) {
    return { key: "rejected", label: "reverted" };
  }
  if (String(action?.name || "").trim() === "sync_ingest") {
    return { key: "approved", label: "incoming push" };
  }
  return { key: "paired", label: "pull" };
};

const SyncLabelText = ({ text, tooltip }) => (
  <span className="knowledge-sync-label-inline" title={tooltip || undefined}>
    {text}
  </span>
);

const KnowledgeSyncTab = () => {
  const importFileInputRef = useRef(null);
  const syncRemoteUrlRef = useRef("");
  const syncRequestRef = useRef(null);
  const syncProgressTimersRef = useRef([]);
  const [loading, setLoading] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);
  const [message, setMessage] = useState("");
  const [importStatus, setImportStatus] = useState("");
  const [overview, setOverview] = useState(null);
  const [deviceDisplayName, setDeviceDisplayName] = useState("");
  const [syncVisibleOnLan, setSyncVisibleOnLan] = useState(false);
  const [syncAutoAcceptPush, setSyncAutoAcceptPush] = useState(false);
  const [syncRemoteUrl, setSyncRemoteUrl] = useState("");
  const [syncLinkToSourceDevice, setSyncLinkToSourceDevice] = useState(false);
  const [syncSourceNamespace, setSyncSourceNamespace] = useState("");
  const [workspaceProfiles, setWorkspaceProfiles] = useState(() => coerceWorkspaceProfiles([]));
  const [activeWorkspaceId, setActiveWorkspaceId] = useState("root");
  const [selectedWorkspaceIds, setSelectedWorkspaceIds] = useState(["root"]);
  const [savedPeers, setSavedPeers] = useState([]);
  const [selectedPeerId, setSelectedPeerId] = useState("");
  const [targetLabel, setTargetLabel] = useState("");
  const [targetScopes, setTargetScopes] = useState(["sync"]);
  const [remoteWorkspaceIds, setRemoteWorkspaceIds] = useState([]);
  const [workspaceMode, setWorkspaceMode] = useState("merge");
  const [localTargetWorkspaceId, setLocalTargetWorkspaceId] = useState("root");
  const [remoteTargetWorkspaceId, setRemoteTargetWorkspaceId] = useState("root");
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [newWorkspaceNamespace, setNewWorkspaceNamespace] = useState("");
  const [newWorkspaceRootPath, setNewWorkspaceRootPath] = useState("");
  const [syncPreview, setSyncPreview] = useState(null);
  const [syncSelections, setSyncSelections] = useState({});
  const [syncItemSelections, setSyncItemSelections] = useState({ pull: {}, push: {} });
  const [syncItemReview, setSyncItemReview] = useState(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncActionBusy, setSyncActionBusy] = useState("");
  const [syncProgress, setSyncProgress] = useState(null);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [pairBusy, setPairBusy] = useState(false);
  const [pairSyncBusy, setPairSyncBusy] = useState(false);
  const [visibilityBusy, setVisibilityBusy] = useState("");
  const [reviewBusyId, setReviewBusyId] = useState("");
  const [pruneLegacyBusy, setPruneLegacyBusy] = useState(false);
  const [peerStatusBusy, setPeerStatusBusy] = useState(false);
  const [peerStatus, setPeerStatus] = useState(null);
  const [syncHistory, setSyncHistory] = useState([]);
  const [undoSyncBusyId, setUndoSyncBusyId] = useState("");
  const [localPairOffer, setLocalPairOffer] = useState(null);
  const [pairCodeInput, setPairCodeInput] = useState("");
  const [exportDefaults, setExportDefaults] = useState(DEFAULT_EXPORT_DEFAULTS);
  const [importReview, setImportReview] = useState({
    file: null,
    detectedFiles: [],
    selectedFiles: {},
    destinationFolder: "",
  });

  const selectedPeer = useMemo(
    () => savedPeers.find((peer) => peer.id === selectedPeerId) || null,
    [savedPeers, selectedPeerId],
  );
  const remoteAddressDirty =
    !!selectedPeer &&
    !!syncRemoteUrl.trim() &&
    syncRemoteUrl.trim() !== String(selectedPeer.remote_url || "").trim();
  const selectedPeerConnected =
    !!selectedPeer &&
    !remoteAddressDirty &&
    !!selectedPeer.remote_device_id &&
    !!peerStatus?.reachable &&
    syncRemoteUrl.trim() === String(selectedPeer.remote_url || "").trim();
  const selectedPeerConnectionLabel = selectedPeerConnected
    ? `Connected to ${
        peerStatus?.display_name
        || selectedPeer?.remote_device_name
        || selectedPeer?.label
        || "paired device"
      }`
    : selectedPeer?.remote_device_id
      ? `Paired with ${
          selectedPeer?.remote_device_name || selectedPeer?.label || "saved device"
        }`
      : "No active remote";
  const recentSyncActions = useMemo(() => syncHistory.slice(0, 8), [syncHistory]);
  const activeSyncLabel = syncActionBusy || (syncBusy ? "preview" : "");

  const clearSyncProgressTimers = () => {
    syncProgressTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    syncProgressTimersRef.current = [];
  };

  const finishSyncProgress = (requestId, updates = {}) => {
    clearSyncProgressTimers();
    if (syncRequestRef.current?.id === requestId) {
      syncRequestRef.current = null;
    }
    setSyncProgress((prev) => {
      if (!prev || prev.id !== requestId) return prev;
      return {
        ...prev,
        ...updates,
        active: false,
      };
    });
  };

  const startSyncProgress = (kind) => {
    const preset = SYNC_PROGRESS_PRESETS[kind] || SYNC_PROGRESS_PRESETS.preview;
    const phases = Array.isArray(preset.phases) && preset.phases.length
      ? preset.phases
      : [{ label: "Working…", progress: 0.2, delayMs: 0 }];
    const requestId = `${kind}-${Date.now()}`;
    const controller =
      typeof AbortController !== "undefined" ? new AbortController() : null;
    clearSyncProgressTimers();
    syncRequestRef.current = { id: requestId, kind, controller };
    setSyncProgress({
      id: requestId,
      kind,
      title: preset.title,
      tone: preset.tone,
      detail: phases[0].label,
      progress: phases[0].progress,
      phaseIndex: 0,
      phaseCount: phases.length,
      active: true,
      note: "Stage-based progress while Float waits on the request.",
    });
    phases.slice(1).forEach((phase, index) => {
      const timer = window.setTimeout(() => {
        setSyncProgress((prev) => {
          if (!prev || prev.id !== requestId || !prev.active) return prev;
          return {
            ...prev,
            detail: phase.label,
            progress: phase.progress,
            phaseIndex: index + 1,
          };
        });
      }, phase.delayMs);
      syncProgressTimersRef.current.push(timer);
    });
    return { controller, requestId };
  };

  const cancelActiveSync = () => {
    const activeRequest = syncRequestRef.current;
    if (!activeRequest?.controller || activeRequest.controller.signal.aborted) return;
    activeRequest.controller.abort("user_cancelled");
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [overviewRes, settingsRes, actionsRes] = await Promise.all([
          axios.get("/api/sync/overview"),
          axios.get("/api/user-settings"),
          axios
            .get("/api/actions", { params: { limit: 30, include_reverted: true } })
            .catch(() => ({ data: { actions: [] } })),
        ]);
        if (cancelled) return;
        const nextOverview = overviewRes?.data || {};
        const peers = coerceSavedPeers(nextOverview?.sync_defaults?.saved_peers);
        const remoteUrl = String(nextOverview?.sync_defaults?.remote_url || "").trim();
        const match = peers.find((peer) => peer.remote_url === remoteUrl) || null;
        const settings = settingsRes?.data || {};
        const workspaceState = nextOverview?.workspaces || {};
        const profiles = coerceWorkspaceProfiles(workspaceState?.profiles);
        const activeId =
          String(workspaceState?.active_workspace_id || "").trim() || "root";
        const selectedIds =
          normalizeWorkspaceIdList(workspaceState?.selected_workspace_ids).length
            ? normalizeWorkspaceIdList(workspaceState?.selected_workspace_ids)
            : [activeId];
        setOverview(nextOverview);
        setWorkspaceProfiles(profiles);
        setActiveWorkspaceId(activeId);
        setSelectedWorkspaceIds(selectedIds);
        setSavedPeers(peers);
        setSelectedPeerId(match?.id || "");
        setTargetLabel(match?.label || "");
        setTargetScopes(match?.scopes || ["sync"]);
        setWorkspaceMode(match?.workspace_mode || "merge");
        setSelectedWorkspaceIds(
          normalizeWorkspaceIdList(match?.local_workspace_ids).length
            ? normalizeWorkspaceIdList(match.local_workspace_ids)
            : selectedIds,
        );
        setRemoteWorkspaceIds(match?.remote_workspace_ids || []);
        setLocalTargetWorkspaceId(match?.local_target_workspace_id || activeId || "root");
        setRemoteTargetWorkspaceId(match?.remote_target_workspace_id || "root");
        setDeviceDisplayName(String(nextOverview?.current_device?.display_name || "").trim());
        setSyncVisibleOnLan(!!nextOverview?.device_access?.visibility?.lan_enabled);
        setSyncAutoAcceptPush(!!nextOverview?.sync_defaults?.auto_accept_push);
        setSyncRemoteUrl(remoteUrl);
        setSyncLinkToSourceDevice(!!nextOverview?.sync_defaults?.link_to_source);
        setSyncSourceNamespace(String(nextOverview?.sync_defaults?.source_namespace || "").trim());
        setSyncHistory(coerceSyncActions(actionsRes?.data?.actions));
        setExportDefaults({
          format: normalizeExportFormat(settings?.export_default_format),
          includeChat:
            typeof settings?.export_default_include_chat === "boolean"
              ? settings.export_default_include_chat
              : true,
          includeThoughts:
            typeof settings?.export_default_include_thoughts === "boolean"
              ? settings.export_default_include_thoughts
              : true,
          includeTools:
            typeof settings?.export_default_include_tools === "boolean"
              ? settings.export_default_include_tools
              : true,
        });
      } catch (error) {
        if (!cancelled) setMessage(extractSyncError(error, "Failed to load sync overview."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  useEffect(() => {
    syncRemoteUrlRef.current = syncRemoteUrl.trim();
    setPeerStatus(null);
    setSyncPreview(null);
    setSyncSelections({});
    setSyncItemSelections({ pull: {}, push: {} });
    setSyncItemReview(null);
    setPeerStatusBusy(false);
    return undefined;
  }, [syncRemoteUrl]);

  useEffect(() => () => {
    clearSyncProgressTimers();
    if (syncRequestRef.current?.controller && !syncRequestRef.current.controller.signal.aborted) {
      syncRequestRef.current.controller.abort("component_unmounted");
    }
    syncRequestRef.current = null;
  }, []);

  useEffect(() => {
    const remoteWorkspaceState = peerStatus?.workspaces || {};
    const remoteProfiles = coerceWorkspaceProfiles(remoteWorkspaceState?.profiles);
    const remoteDefaults = normalizeWorkspaceIdList(remoteWorkspaceState?.selected_workspace_ids);
    if (!remoteWorkspaceIds.length && remoteDefaults.length) {
      setRemoteWorkspaceIds(remoteDefaults);
    }
    if (
      !remoteTargetWorkspaceId &&
      (remoteWorkspaceState?.active_workspace_id || remoteProfiles[0]?.id)
    ) {
      setRemoteTargetWorkspaceId(
        String(remoteWorkspaceState?.active_workspace_id || remoteProfiles[0]?.id || "root"),
      );
    }
  }, [peerStatus, remoteTargetWorkspaceId, remoteWorkspaceIds.length]);

  const syncOptionsPayload = useMemo(() => {
    const payload = { link_to_source: !!syncLinkToSourceDevice };
    const namespace = syncSourceNamespace.trim() || deviceDisplayName.trim();
    if (namespace) payload.source_namespace = namespace;
    return payload;
  }, [deviceDisplayName, syncLinkToSourceDevice, syncSourceNamespace]);

  const persistSyncPreferences = async (overrides = {}, successMessage = "") => {
    const resolvedSourceNamespace = syncSourceNamespace.trim() || deviceDisplayName.trim();
    await axios.post("/api/user-settings", {
      device_display_name: deviceDisplayName.trim(),
      sync_visible_on_lan: !!syncVisibleOnLan,
      sync_auto_accept_push: !!syncAutoAcceptPush,
      sync_remote_url: syncRemoteUrl.trim(),
      sync_link_to_source_device: !!syncLinkToSourceDevice,
      sync_source_namespace: resolvedSourceNamespace,
      sync_saved_peers: savedPeers,
      workspace_profiles: workspaceProfiles.filter((profile) => profile.id !== "root"),
      active_workspace_id: activeWorkspaceId || "root",
      sync_selected_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds).length
        ? normalizeWorkspaceIdList(selectedWorkspaceIds)
        : [activeWorkspaceId || "root"],
      ...overrides,
    });
    if (successMessage) setMessage(successMessage);
  };

  const buildPairedDevicePayload = () =>
    selectedPeerId
      ? {
          id: selectedPeerId,
          label: targetLabel.trim() || syncRemoteUrl.trim() || selectedPeer?.label || "Unnamed device",
          remote_url: syncRemoteUrl.trim(),
          scopes: normalizePeerScopes(targetScopes),
          remote_device_id: selectedPeer?.remote_device_id || "",
          public_key: selectedPeer?.public_key || "",
          remote_public_key: selectedPeer?.remote_public_key || "",
          remote_device_name: selectedPeer?.remote_device_name || "",
          last_used_at: selectedPeer?.last_used_at || "",
          local_workspace_ids: normalizeWorkspaceIdList(selectedPeer?.local_workspace_ids || selectedWorkspaceIds),
          remote_workspace_ids: normalizeWorkspaceIdList(selectedPeer?.remote_workspace_ids || remoteWorkspaceIds),
          workspace_mode: selectedPeer?.workspace_mode || workspaceMode,
          local_target_workspace_id:
            selectedPeer?.local_target_workspace_id || localTargetWorkspaceId || activeWorkspaceId || "root",
          remote_target_workspace_id:
            selectedPeer?.remote_target_workspace_id || remoteTargetWorkspaceId || "root",
        }
      : null;

  const mergePairedDeviceRecord = (record) => {
    const next = coerceSavedPeers(record ? [record] : [])[0];
    if (!next?.id) return;
    setSavedPeers((prev) => {
      const exists = prev.some((peer) => peer.id === next.id);
      return exists
        ? prev.map((peer) => (peer.id === next.id ? { ...peer, ...next } : peer))
        : [next, ...prev];
    });
  };

  const buildSyncItemSelectionState = (previewPayload) => {
    const nextSelections = { pull: {}, push: {} };
    const nextPullSections = Array.isArray(previewPayload?.pull_sections)
      ? previewPayload.pull_sections
      : Array.isArray(previewPayload?.sections)
        ? previewPayload.sections
        : [];
    const nextPushSections = Object.fromEntries(
      (Array.isArray(previewPayload?.push_sections) ? previewPayload.push_sections : [])
        .filter((section) => section?.key)
        .map((section) => [section.key, section]),
    );
    nextPullSections.forEach((section) => {
      if (!section?.key) return;
      nextSelections.pull[section.key] = syncSectionActionableItems(section, "pull").map(
        (item) => item?.selection_id || item?.resource_id,
      );
      const pushSection = nextPushSections[section.key] || section;
      nextSelections.push[section.key] = syncSectionActionableItems(pushSection, "push").map(
        (item) => item?.selection_id || item?.resource_id,
      );
    });
    return nextSelections;
  };

  const updateSyncItemSelection = (direction, sectionKey, nextIds) => {
    setSyncItemSelections((prev) => ({
      ...prev,
      [direction]: {
        ...(prev?.[direction] || {}),
        [sectionKey]: normalizeSyncSelectionIds(nextIds),
      },
    }));
  };

  const toggleSyncReviewItem = (direction, sectionKey, selectionId) => {
    setSyncItemSelections((prev) => {
      const current = normalizeSyncSelectionIds(prev?.[direction]?.[sectionKey]);
      const next = current.includes(selectionId)
        ? current.filter((item) => item !== selectionId)
        : [...current, selectionId];
      return {
        ...prev,
        [direction]: {
          ...(prev?.[direction] || {}),
          [sectionKey]: next,
        },
      };
    });
  };

  const updateWorkspaceProfile = (workspaceId, updates) => {
    setWorkspaceProfiles((prev) =>
      coerceWorkspaceProfiles(
        prev.map((profile) => {
          if (profile.id !== workspaceId) return profile;
          if (profile.id === "root") {
            return {
              ...profile,
              name:
                typeof updates?.name === "string" && updates.name.trim()
                  ? updates.name.trim()
                  : profile.name,
            };
          }
          const nextName =
            typeof updates?.name === "string" ? updates.name : profile.name;
          const nextNamespace =
            typeof updates?.namespace === "string"
              ? cleanWorkspaceNamespace(updates.namespace)
              : profile.namespace;
          const nextRootPath =
            typeof updates?.root_path === "string" ? updates.root_path : profile.root_path;
          return {
            ...profile,
            ...updates,
            name: String(nextName || "").trim() || profile.name,
            slug: slugifyWorkspaceToken(nextName || profile.name || workspaceId),
            namespace: nextNamespace,
            root_path: String(nextRootPath || "").trim() || profile.root_path,
          };
        }),
      ),
    );
  };

  const removeWorkspaceProfile = (workspaceId) => {
    if (!workspaceId || workspaceId === "root") return;
    setWorkspaceProfiles((prev) => prev.filter((profile) => profile.id !== workspaceId));
    setSelectedWorkspaceIds((prev) => {
      const next = prev.filter((id) => id !== workspaceId);
      return next.length ? next : ["root"];
    });
    setRemoteWorkspaceIds((prev) => prev.filter((id) => id !== workspaceId));
    if (activeWorkspaceId === workspaceId) setActiveWorkspaceId("root");
    if (localTargetWorkspaceId === workspaceId) setLocalTargetWorkspaceId("root");
  };

  const addWorkspaceProfile = () => {
    const name = String(newWorkspaceName || "").trim();
    if (!name) {
      setMessage("Enter a workspace name first.");
      return;
    }
    const namespace =
      cleanWorkspaceNamespace(newWorkspaceNamespace) || slugifyWorkspaceToken(name);
    const rootPath =
      String(newWorkspaceRootPath || "").trim() || `data/files/workspace/${namespace}`;
    const nextProfile = {
      id: buildWorkspaceId(),
      name,
      slug: slugifyWorkspaceToken(name),
      namespace,
      root_path: rootPath,
      kind: "local",
      imported: false,
      is_root: false,
      source_peer_id: "",
      source_device_name: "",
      source_workspace_id: "",
      source_workspace_name: "",
    };
    setWorkspaceProfiles((prev) => coerceWorkspaceProfiles([...prev, nextProfile]));
    setSelectedWorkspaceIds((prev) =>
      normalizeWorkspaceIdList([...prev, nextProfile.id]).length
        ? normalizeWorkspaceIdList([...prev, nextProfile.id])
        : [activeWorkspaceId || "root"],
    );
    setNewWorkspaceName("");
    setNewWorkspaceNamespace("");
    setNewWorkspaceRootPath("");
    setMessage(`Workspace ${name} added.`);
  };

  const resetPairEditor = () => {
    setSelectedPeerId("");
    setTargetLabel("");
    setTargetScopes(["sync"]);
    setSyncRemoteUrl("");
    setRemoteWorkspaceIds([]);
    setWorkspaceMode("merge");
    setLocalTargetWorkspaceId(activeWorkspaceId || "root");
    setRemoteTargetWorkspaceId("root");
    setSyncPreview(null);
    setMessage("");
  };

  const saveDeviceSettings = async () => {
    setSavingPrefs(true);
    setMessage("");
    const resolvedSourceNamespace = syncSourceNamespace.trim() || deviceDisplayName.trim();
    const normalizedWorkspaceProfiles = coerceWorkspaceProfiles(workspaceProfiles);
    const normalizedSelectedWorkspaceIds =
      normalizeWorkspaceIdList(selectedWorkspaceIds).length
        ? normalizeWorkspaceIdList(selectedWorkspaceIds)
        : [activeWorkspaceId || "root"];
    if (resolvedSourceNamespace && resolvedSourceNamespace !== syncSourceNamespace) {
      setSyncSourceNamespace(resolvedSourceNamespace);
    }
    setWorkspaceProfiles(normalizedWorkspaceProfiles);
    setSelectedWorkspaceIds(normalizedSelectedWorkspaceIds);
    try {
      await persistSyncPreferences(
        {
          workspace_profiles: normalizedWorkspaceProfiles.filter((profile) => profile.id !== "root"),
          active_workspace_id: activeWorkspaceId || "root",
          sync_selected_workspace_ids: normalizedSelectedWorkspaceIds,
        },
        "Device and sync defaults saved.",
      );
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to save sync defaults."));
    } finally {
      setSavingPrefs(false);
    }
  };

  const updateLanVisibility = async (nextValue) => {
    const previous = syncVisibleOnLan;
    setSyncVisibleOnLan(nextValue);
    if (!nextValue) setLocalPairOffer(null);
    setVisibilityBusy("lan");
    setMessage("");
    try {
      await persistSyncPreferences(
        { sync_visible_on_lan: !!nextValue },
        nextValue ? "LAN visibility enabled." : "LAN visibility disabled.",
      );
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setSyncVisibleOnLan(previous);
      setMessage(extractSyncError(error, "Failed to update LAN visibility."));
    } finally {
      setVisibilityBusy("");
    }
  };

  const updatePushReviewMode = async (nextValue) => {
    const previous = syncAutoAcceptPush;
    setSyncAutoAcceptPush(nextValue);
    setVisibilityBusy("push-review");
    setMessage("");
    try {
      await persistSyncPreferences(
        { sync_auto_accept_push: !!nextValue },
        nextValue
          ? "Paired devices can now push here without review."
          : "Incoming pushes now pause for review on this device.",
      );
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setSyncAutoAcceptPush(previous);
      setMessage(extractSyncError(error, "Failed to update push review mode."));
    } finally {
      setVisibilityBusy("");
    }
  };

  const upsertSavedPeer = async () => {
    const remoteUrl = syncRemoteUrl.trim();
    if (!remoteUrl) {
      setMessage("Enter a device URL first.");
      return;
    }
    if (importModeInvalid) {
      setMessage("Import mode currently supports one local and one remote source workspace.");
      return;
    }
    const nextPeer = {
      ...(selectedPeer || {}),
      id: selectedPeerId || buildPeerId(),
      label: targetLabel.trim() || remoteUrl,
      remote_url: remoteUrl,
      scopes: normalizePeerScopes(targetScopes),
      local_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds),
      remote_workspace_ids: normalizeWorkspaceIdList(remoteWorkspaceIds),
      workspace_mode: workspaceMode,
      local_target_workspace_id: localTargetWorkspaceId || activeWorkspaceId || "root",
      remote_target_workspace_id: remoteTargetWorkspaceId || "root",
    };
    const nextPeers = savedPeers.some((peer) => peer.id === nextPeer.id)
      ? savedPeers.map((peer) => (peer.id === nextPeer.id ? { ...peer, ...nextPeer } : peer))
      : [nextPeer, ...savedPeers];
    setSavedPeers(nextPeers);
    setSelectedPeerId(nextPeer.id);
    setTargetLabel(nextPeer.label);
    setTargetScopes(nextPeer.scopes);
    setSavingPrefs(true);
    setMessage("");
    try {
      await persistSyncPreferences(
        { sync_saved_peers: nextPeers, sync_remote_url: remoteUrl },
        `${selectedPeerId ? "Updated" : "Saved"} paired device ${nextPeer.label}.`,
      );
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to save paired device."));
    } finally {
      setSavingPrefs(false);
    }
  };

  const removeSavedPeer = async (peer) => {
    if (!peer?.id || !window.confirm(`Remove paired device '${peer.label}'?`)) return;
    const nextPeers = savedPeers.filter((entry) => entry.id !== peer.id);
    const removingSelected = peer.id === selectedPeerId;
    if (removingSelected) resetPairEditor();
    setSavedPeers(nextPeers);
    setSavingPrefs(true);
    setMessage("");
    try {
      await persistSyncPreferences(
        { sync_saved_peers: nextPeers, sync_remote_url: removingSelected ? "" : syncRemoteUrl.trim() },
        "Paired device removed.",
      );
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to remove paired device."));
    } finally {
      setSavingPrefs(false);
    }
  };

  const checkPeerStatus = async () => {
    const remoteUrl = syncRemoteUrl.trim();
    if (!remoteUrl) {
      setPeerStatus(null);
      setMessage("Enter the other Float instance URL first.");
      return null;
    }
    setPeerStatusBusy(true);
    try {
      const res = await axios.post("/api/sync/peer/status", { remote_url: remoteUrl });
      if (syncRemoteUrlRef.current === remoteUrl) {
        setPeerStatus(res?.data || null);
      }
      return res?.data || null;
    } catch (error) {
      const nextStatus = {
        reachable: false,
        error: extractSyncError(error, "Remote device is not reachable right now."),
      };
      if (syncRemoteUrlRef.current === remoteUrl) {
        setPeerStatus(nextStatus);
      }
      return nextStatus;
    } finally {
      setPeerStatusBusy(false);
    }
  };

  const previewSync = async () => {
    const remoteUrl = syncRemoteUrl.trim();
    if (!remoteUrl) {
      setMessage("Enter the other Float instance URL first.");
      return;
    }
    if (importModeInvalid) {
      setMessage("Import mode currently supports one local and one remote source workspace.");
      return;
    }
    if (selectedPeerId && !normalizePeerScopes(targetScopes).includes("sync")) {
      setMessage("Paired devices used here must include the sync scope.");
      return;
    }
    setSyncBusy(true);
    setMessage("");
    const { controller, requestId } = startSyncProgress("preview");
    try {
      const res = await axios.post(
        "/api/sync/plan",
        {
          remote_url: remoteUrl,
          local_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds),
          remote_workspace_ids: normalizeWorkspaceIdList(remoteWorkspaceIds),
          workspace_mode: workspaceMode,
          local_target_workspace_id: localTargetWorkspaceId || activeWorkspaceId || "root",
          remote_target_workspace_id: remoteTargetWorkspaceId || "root",
          paired_device: buildPairedDevicePayload(),
          ...syncOptionsPayload,
        },
        controller ? { signal: controller.signal } : undefined,
      );
      const previewPayload = res?.data || null;
      const sections = Array.isArray(previewPayload?.pull_sections)
        ? previewPayload.pull_sections
        : Array.isArray(previewPayload?.sections)
          ? previewPayload.sections
          : [];
      setSyncPreview(previewPayload);
      setSyncSelections(
        sections.reduce((acc, section) => {
          if (section?.key) acc[section.key] = !!section.selected_by_default;
          return acc;
        }, {}),
      );
      setSyncItemSelections(buildSyncItemSelectionState(previewPayload));
      setSyncItemReview(null);
      setPeerStatus((prev) => ({
        ...(prev || {}),
        reachable: true,
        display_name: String(previewPayload?.remote?.display_name || "").trim(),
        hostname: String(previewPayload?.remote?.hostname || "").trim(),
        instance_base: String(previewPayload?.remote?.base_url || remoteUrl).trim(),
        workspaces: previewPayload?.workspaces?.remote || prev?.workspaces || { profiles: [] },
      }));
      if (!targetLabel.trim()) {
        setTargetLabel(String(previewPayload?.remote?.hostname || remoteUrl).trim());
      }
      mergePairedDeviceRecord(previewPayload?.paired_device);
      await persistSyncPreferences({ sync_remote_url: remoteUrl });
      finishSyncProgress(requestId, {
        detail: "Preview ready.",
        progress: 1,
        note: "Select the sections you want before pulling or pushing.",
      });
    } catch (error) {
      if (isSyncRequestCancelled(error, controller)) {
        finishSyncProgress(requestId, {
          detail: "Preview stopped.",
          progress: 0,
          note: "No sync changes were applied.",
        });
        if (controller?.signal?.reason === "user_cancelled") {
          setMessage("Sync preview cancelled.");
        }
        return;
      }
      finishSyncProgress(requestId, {
        detail: "Preview failed.",
        progress: 0,
        note: extractSyncError(error, "Failed to preview instance sync."),
      });
      setMessage(extractSyncError(error, "Failed to preview instance sync."));
    } finally {
      setSyncBusy(false);
    }
  };

  const applySync = async (direction) => {
    const sectionSource =
      direction === "push"
        ? pullSections.map((section) => pushSectionMap[section.key] || section)
        : pullSections;
    const sections = [];
    const itemSelections = {};
    sectionSource.forEach((section) => {
      const sectionKey = String(section?.key || "").trim();
      if (!sectionKey || !syncSelections[sectionKey]) return;
      const actionableItems = syncSectionActionableItems(section, direction);
      if (!actionableItems.length) return;
      const allowedSelectionIds = new Set(
        actionableItems.map((item) => String(item?.selection_id || item?.resource_id || "").trim()),
      );
      const selectedIds = normalizeSyncSelectionIds(syncItemSelections?.[direction]?.[sectionKey]).filter(
        (itemId) => allowedSelectionIds.has(itemId),
      );
      if (!selectedIds.length) return;
      sections.push(sectionKey);
      itemSelections[sectionKey] = selectedIds;
    });
    if (!sections.length) {
      setMessage(
        direction === "push"
          ? "Choose at least one item to push."
          : "Choose at least one item to pull.",
      );
      return;
    }
    if (importModeInvalid) {
      setMessage("Import mode currently supports one local and one remote source workspace.");
      return;
    }
    if (selectedPeerId && !normalizePeerScopes(targetScopes).includes("sync")) {
      setMessage("Paired devices used here must include the sync scope.");
      return;
    }
    setSyncActionBusy(direction);
    setMessage("");
    setSyncItemReview(null);
    const { controller, requestId } = startSyncProgress(direction);
    try {
      const res = await axios.post(
        "/api/sync/apply",
        {
          remote_url: syncRemoteUrl.trim(),
          direction,
          sections,
          local_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds),
          remote_workspace_ids: normalizeWorkspaceIdList(remoteWorkspaceIds),
          workspace_mode: workspaceMode,
          local_target_workspace_id: localTargetWorkspaceId || activeWorkspaceId || "root",
          remote_target_workspace_id: remoteTargetWorkspaceId || "root",
          item_selections: itemSelections,
          paired_device: buildPairedDevicePayload(),
          ...syncOptionsPayload,
        },
        controller ? { signal: controller.signal } : undefined,
      );
      mergePairedDeviceRecord(res?.data?.paired_device);
      const sectionMap = res?.data?.result?.sections;
      if (direction === "push" && res?.data?.result?.status === "pending_review") {
        finishSyncProgress(requestId, {
          detail: "Push queued for review.",
          progress: 1,
          note: "The other device needs to approve it before applying changes.",
        });
        setMessage(
          `Push queued for review on ${res?.data?.remote || syncRemoteUrl.trim()}. Review request ${res?.data?.result?.review_request_id || ""}`.trim(),
        );
        setRefreshToken((value) => value + 1);
        return;
      }
      const effectiveNamespace =
        res?.data?.effective_namespace || res?.data?.result?.effective_namespace;
      finishSyncProgress(requestId, {
        detail: direction === "push" ? "Push complete." : "Pull complete.",
        progress: 1,
        note:
          direction === "push"
            ? "The remote device has finished processing the selected sections."
            : "Local sync finished and the refreshed data is ready here.",
      });
      setMessage(
        direction === "push"
          ? `Push complete. ${summarizeSyncSections(sectionMap)}${
              effectiveNamespace ? ` Remote copy linked under ${effectiveNamespace}/.` : ""
            }`
          : `Pull complete. ${summarizeSyncSections(sectionMap)}${
              effectiveNamespace ? ` Stored under ${effectiveNamespace}/.` : ""
            }`,
      );
      setRefreshToken((value) => value + 1);
    } catch (error) {
      if (isSyncRequestCancelled(error, controller)) {
        finishSyncProgress(requestId, {
          detail: direction === "push" ? "Push cancelled." : "Pull cancelled.",
          progress: 0,
          note: "The request was stopped before completion.",
        });
        if (controller?.signal?.reason === "user_cancelled") {
          setMessage(direction === "push" ? "Push cancelled." : "Pull cancelled.");
        }
        return;
      }
      finishSyncProgress(requestId, {
        detail: direction === "push" ? "Push failed." : "Pull failed.",
        progress: 0,
        note: extractSyncError(
          error,
          direction === "push"
            ? "Failed to push data to the remote Float instance."
            : "Failed to pull data from the remote Float instance.",
        ),
      });
      setMessage(
        extractSyncError(
          error,
          direction === "push"
            ? "Failed to push data to the remote Float instance."
            : "Failed to pull data from the remote Float instance.",
        ),
      );
    } finally {
      setSyncActionBusy("");
    }
  };

  const revokeInboundDevice = async (device) => {
    if (!device?.id || !window.confirm(`Revoke trusted device '${device.name}' from this instance?`)) {
      return;
    }
    setMessage("");
    try {
      await axios.delete(`/api/devices/${encodeURIComponent(device.id)}`);
      setMessage(`Revoked local trust for ${device.name}.`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to revoke trusted device."));
    }
  };

  const createPairingOffer = async () => {
    if (!syncVisibleOnLan) {
      setMessage("Turn on LAN visibility before generating a pairing code for another device.");
      return;
    }
    setPairBusy(true);
    setMessage("");
    try {
      const res = await axios.post("/api/pairing/offers", {
        requested_scopes: ["sync"],
      });
      setLocalPairOffer(res?.data?.offer || null);
      setMessage("Pairing code created.");
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to create pairing code."));
    } finally {
      setPairBusy(false);
    }
  };

  const pairWithCode = async () => {
    const remoteUrl = syncRemoteUrl.trim();
    const code = pairCodeInput.trim().toUpperCase();
    if (!remoteUrl || !code) {
      setMessage("Enter the remote URL and pairing code first.");
      return;
    }
    if (importModeInvalid) {
      setMessage("Import mode currently supports one local and one remote source workspace.");
      return;
    }
    setPairBusy(true);
    setMessage("");
    try {
      const res = await axios.post("/api/sync/pair", {
        peer_id: selectedPeerId || undefined,
        remote_url: remoteUrl,
        code,
        label: targetLabel.trim() || undefined,
        scopes: normalizePeerScopes(targetScopes),
        local_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds),
        remote_workspace_ids: normalizeWorkspaceIdList(remoteWorkspaceIds),
        workspace_mode: workspaceMode,
        local_target_workspace_id: localTargetWorkspaceId || activeWorkspaceId || "root",
        remote_target_workspace_id: remoteTargetWorkspaceId || "root",
      });
      const paired = coerceSavedPeers(res?.data?.paired_device ? [res.data.paired_device] : [])[0];
      if (paired) {
        mergePairedDeviceRecord(paired);
        setSelectedPeerId(paired.id);
        setTargetLabel(paired.label);
        setTargetScopes(paired.scopes);
        setPairCodeInput("");
        setRefreshToken((value) => value + 1);
        setMessage(`Paired with ${paired.label}.`);
      } else {
        setMessage("Pairing completed, but the returned device record was incomplete.");
      }
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to pair devices."));
    } finally {
      setPairBusy(false);
    }
  };

  const syncPairTrust = async (peer) => {
    if (!peer) return;
    setPairSyncBusy(true);
    setMessage("");
    try {
      const res = await axios.post("/api/sync/pair/update", { paired_device: peer });
      mergePairedDeviceRecord(res?.data?.paired_device);
      setRefreshToken((value) => value + 1);
      setMessage(`Updated remote trust for ${peer.label}.`);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to update remote trust."));
    } finally {
      setPairSyncBusy(false);
    }
  };

  const revertSyncAction = async (action) => {
    if (!action?.id) return;
    setUndoSyncBusyId(action.id);
    setMessage("");
    try {
      const res = await axios.post("/api/actions/revert", { action_ids: [action.id] });
      const revertedIds = Array.isArray(res?.data?.reverted_action_ids)
        ? res.data.reverted_action_ids
        : [];
      if (!revertedIds.length) {
        setMessage("This sync record no longer has anything to undo.");
      } else {
        setMessage(`Reverted ${action.summary || "sync activity"}.`);
      }
      setSyncPreview(null);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to revert sync activity."));
    } finally {
      setUndoSyncBusyId("");
    }
  };

  const revokeRemotePair = async (peer) => {
    if (!peer || !window.confirm(`Revoke '${peer.label}' on the remote device and remove the pair here?`)) {
      return;
    }
    setPairSyncBusy(true);
    setMessage("");
    try {
      await axios.post("/api/sync/pair/revoke", {
        paired_device: peer,
        remove_local_pair: true,
      });
      setSavedPeers((prev) => prev.filter((entry) => entry.id !== peer.id));
      if (selectedPeerId === peer.id) resetPairEditor();
      setRefreshToken((value) => value + 1);
      setMessage(`Revoked ${peer.label} remotely and removed the pair locally.`);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to revoke remote pair."));
    } finally {
      setPairSyncBusy(false);
    }
  };

  const approvePendingReview = async (review) => {
    if (!review?.id) return;
    setReviewBusyId(review.id);
    setMessage("");
    try {
      const res = await axios.post(`/api/sync/reviews/${encodeURIComponent(review.id)}/approve`, {});
      const resultSections = res?.data?.result?.sections || {};
      setMessage(`Approved push from ${review.source_label}. ${summarizeSyncSections(resultSections)}`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to approve incoming push."));
    } finally {
      setReviewBusyId("");
    }
  };

  const rejectPendingReview = async (review) => {
    if (!review?.id) return;
    setReviewBusyId(review.id);
    setMessage("");
    try {
      await axios.post(`/api/sync/reviews/${encodeURIComponent(review.id)}/reject`, {});
      setMessage(`Rejected push from ${review.source_label}.`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to reject incoming push."));
    } finally {
      setReviewBusyId("");
    }
  };

  const pruneLegacyDevices = async () => {
    setPruneLegacyBusy(true);
    setMessage("");
    try {
      const res = await axios.post("/api/devices/prune-legacy");
      setMessage(`Removed ${res?.data?.removed || 0} legacy browser records.`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to prune legacy trusted-device records."));
    } finally {
      setPruneLegacyBusy(false);
    }
  };

  const handleExportAll = async () => {
    setExportBusy(true);
    setMessage("");
    try {
      const res = await axios.get("/api/conversations/export-all", {
        params: {
          format: normalizeExportFormat(exportDefaults.format),
          include_chat: !!exportDefaults.includeChat,
          include_thoughts: !!exportDefaults.includeThoughts,
          include_tools: !!exportDefaults.includeTools,
        },
        responseType: "blob",
      });
      const disposition = res.headers?.["content-disposition"] || "";
      let filename = `float-conversations-${new Date()
        .toISOString()
        .replace(/[:.]/g, "")
        .replace("T", "-")
        .replace("Z", "")}.zip`;
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      if (match?.[1]) filename = match[1];
      if (!filename.toLowerCase().endsWith(".zip")) filename = `${filename}.zip`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setMessage("Export created.");
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to export conversations."));
    } finally {
      setExportBusy(false);
    }
  };

  const clearImportReview = () =>
    setImportReview({
      file: null,
      detectedFiles: [],
      selectedFiles: {},
      destinationFolder: "",
    });

  const triggerImportPicker = () => {
    setImportStatus("");
    clearImportReview();
    if (importFileInputRef.current) {
      importFileInputRef.current.value = "";
      importFileInputRef.current.click();
    }
  };

  const previewImportCandidates = async (file) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setImportBusy(true);
    setImportStatus("Detecting import candidates...");
    try {
      const response = await axios.post("/api/conversations/import/preview", formData);
      const detectedFiles = Array.isArray(response?.data?.detected_files)
        ? response.data.detected_files
        : [];
      if (!detectedFiles.length) {
        setImportStatus("No importable files detected in this archive.");
        return;
      }
      const selectedFiles = {};
      detectedFiles.forEach((item) => {
        const path = String(item?.path || item?.name || "").trim();
        if (path) selectedFiles[path] = true;
      });
      setImportReview({ file, detectedFiles, selectedFiles, destinationFolder: "" });
      setImportStatus("");
    } catch (error) {
      setImportStatus(extractSyncError(error, "Import preview failed."));
    } finally {
      setImportBusy(false);
    }
  };

  const uploadConversationImport = async ({ file, selectedFiles = null, destinationFolder = "" }) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("format", inferImportFormatFromFilename(file.name));
    if (Array.isArray(selectedFiles) && selectedFiles.length) {
      formData.append("selected_files", JSON.stringify(selectedFiles));
    }
    if (destinationFolder) formData.append("destination_folder", destinationFolder);
    setImportBusy(true);
    setImportStatus("Importing...");
    try {
      const res = await axios.post("/api/conversations/import", formData);
      const imported = Array.isArray(res?.data?.imports) ? res.data.imports : [];
      setImportStatus(
        imported.length > 1
          ? `Imported ${imported.length} conversations (${res?.data?.message_count || 0} messages).`
          : `Imported ${
              String(imported?.[0]?.name || res?.data?.name || "").trim() || "archive"
            } (${res?.data?.message_count || 0} messages).`,
      );
      clearImportReview();
    } catch (error) {
      setImportStatus(extractSyncError(error, "Import failed."));
    } finally {
      setImportBusy(false);
    }
  };

  const handleImportFileChange = (event) => {
    const file = event?.target?.files?.[0];
    if (!file) return;
    const format = inferImportFormatFromFilename(file.name);
    if (format === "zip" || format === "json") {
      previewImportCandidates(file);
      return;
    }
    uploadConversationImport({ file });
  };

  const importReviewSelectedCount = Object.values(importReview.selectedFiles || {}).filter(Boolean)
    .length;
  const importReviewAllSelected =
    importReview.detectedFiles.length > 0 &&
    importReviewSelectedCount === importReview.detectedFiles.length;
  const deviceAccess = overview?.device_access || {};
  const advertisedUrls = deviceAccess?.advertised_urls || {};
  const lanUrl = String(advertisedUrls?.lan || "").trim();
  const internetUrl = String(advertisedUrls?.internet || "").trim();
  const localWorkspaceProfiles = coerceWorkspaceProfiles(workspaceProfiles);
  const remoteWorkspaceState =
    peerStatus?.workspaces || syncPreview?.workspaces?.remote || { profiles: [] };
  const remoteWorkspaceProfiles = coerceWorkspaceProfiles(remoteWorkspaceState?.profiles);
  const inboundDevices = Array.isArray(overview?.inbound_devices) ? overview.inbound_devices : [];
  const legacyInboundDevices = Array.isArray(overview?.legacy_inbound_devices)
    ? overview.legacy_inbound_devices
    : [];
  const pendingReviews = Array.isArray(overview?.sync_reviews?.pending) ? overview.sync_reviews.pending : [];
  const recentReviews = Array.isArray(overview?.sync_reviews?.recent) ? overview.sync_reviews.recent : [];
  const deviceCounts = overview?.device_counts || {};
  const pullSections = Array.isArray(syncPreview?.pull_sections)
    ? syncPreview.pull_sections
    : Array.isArray(syncPreview?.sections)
      ? syncPreview.sections
      : [];
  const pushSectionMap = Object.fromEntries(
    Array.isArray(syncPreview?.push_sections)
      ? syncPreview.push_sections.map((section) => [section.key, section])
      : [],
  );
  const remotePreviewLabel =
    String(syncPreview?.remote?.display_name || "").trim()
    || String(syncPreview?.remote?.hostname || "").trim()
    || String(syncPreview?.remote?.base_url || "").trim()
    || "remote";
  const syncItemReviewDirection = String(syncItemReview?.direction || "").trim().toLowerCase();
  const syncItemReviewSectionKey = String(syncItemReview?.sectionKey || "").trim();
  const syncItemReviewSection =
    syncItemReviewDirection === "push"
      ? pushSectionMap[syncItemReviewSectionKey] || null
      : pullSections.find((section) => section?.key === syncItemReviewSectionKey) || null;
  const syncItemReviewItems =
    syncItemReviewSection && (syncItemReviewDirection === "pull" || syncItemReviewDirection === "push")
      ? syncSectionActionableItems(syncItemReviewSection, syncItemReviewDirection)
      : [];
  const syncItemReviewSelectedIds = normalizeSyncSelectionIds(
    syncItemSelections?.[syncItemReviewDirection]?.[syncItemReviewSectionKey],
  );
  const syncItemReviewAllSelected =
    syncItemReviewItems.length > 0
    && syncItemReviewSelectedIds.length === syncItemReviewItems.length;
  const importModeInvalid =
    workspaceMode === "import" &&
    (normalizeWorkspaceIdList(selectedWorkspaceIds).length !== 1
      || normalizeWorkspaceIdList(remoteWorkspaceIds).length !== 1);

  if (loading) return <div className="knowledge-sync-tab">Loading sync overview...</div>;

  return (
    <div className="knowledge-sync-tab">
      <div className="knowledge-sync-head">
        <div>
          <h3>Devices and sync</h3>
          <p className="status-note">
            Name this device, pair another device, preview the diff, then pull or push.
          </p>
        </div>
        <button type="button" className="icon-btn" onClick={() => setRefreshToken((value) => value + 1)}>
          Refresh
        </button>
      </div>

      {message ? <p className="status-note">{message}</p> : null}
      {syncProgress ? (
        <section
          className={`knowledge-sync-progress knowledge-sync-progress--${syncProgress.tone || "preview"}`}
          aria-live="polite"
        >
          <div className="knowledge-sync-section-head">
            <div className="knowledge-sync-section-stack">
              <strong>{syncProgress.title}</strong>
              <span className="status-note">{syncProgress.detail}</span>
            </div>
            <div className="knowledge-sync-head-actions">
              <span className={`knowledge-sync-target-status is-${syncProgress.active ? "pending" : "paired"}`}>
                {syncProgress.active ? "running" : "ready"}
              </span>
              {syncProgress.active ? (
                <button type="button" className="icon-btn" onClick={cancelActiveSync}>
                  Stop {activeSyncLabel || "sync"}
                </button>
              ) : null}
            </div>
          </div>
          <div className="download-progress-track small knowledge-sync-progress-track">
            <div
              className="download-progress-fill knowledge-sync-progress-fill"
              style={{ width: `${Math.max(0, Math.min(100, Math.round((syncProgress.progress || 0) * 100)))}%` }}
            />
          </div>
          <div className="knowledge-sync-target-meta">
            <span>
              Step {Math.min((syncProgress.phaseIndex || 0) + 1, syncProgress.phaseCount || 1)} of{" "}
              {syncProgress.phaseCount || 1}
            </span>
            {syncProgress.note ? <span>{syncProgress.note}</span> : null}
          </div>
        </section>
      ) : null}

      <div className="knowledge-sync-grid">
        <section className="knowledge-sync-card">
          <div className="knowledge-sync-section-head">
            <h4>
              <SyncLabelText
                text="Current device"
                tooltip="This Float instance keeps its own device identity, pairing state, and advertised sync addresses here."
              />
            </h4>
          </div>
          <label className="field-label" htmlFor="sync-current-device-name">
            <SyncLabelText
              text="Device name"
              tooltip="Human-readable name shown in pairing, trusted-device records, and sync previews."
            />
          </label>
          <input
            id="sync-current-device-name"
            type="text"
            value={deviceDisplayName}
            onChange={(event) => setDeviceDisplayName(event.target.value)}
            placeholder="desktop"
          />
          <label className="field-label" htmlFor="sync-current-namespace">
            <SyncLabelText
              text="Source namespace"
              tooltip="Optional prefix used when linked sync data should stay namespaced by source device instead of merging directly."
            />
          </label>
          <input
            id="sync-current-namespace"
            type="text"
            value={syncSourceNamespace}
            onChange={(event) => setSyncSourceNamespace(event.target.value)}
            placeholder="desktop"
          />
          <label className="knowledge-sync-inline-toggle">
            <input
              type="checkbox"
              checked={syncLinkToSourceDevice}
              onChange={(event) => setSyncLinkToSourceDevice(event.target.checked)}
            />
            <SyncLabelText
              text="Link synced data to the source namespace by default"
              tooltip="When enabled, pulled data stays grouped under a source folder or namespace instead of merging directly into the same top-level records."
            />
          </label>
          <div className="knowledge-sync-card-subtle">
            <strong
              title="Workspace profiles describe named roots and namespaces for sync and document organization. The root workspace stays un-namespaced; imported nested workspaces appear here automatically after import-mode pulls."
            >
              Workspaces
            </strong>
            <div className="knowledge-sync-workspace-list">
              {localWorkspaceProfiles.map((profile) => {
                const imported = profile.imported === true || profile.kind === "synced";
                return (
                  <article
                    key={`workspace-${profile.id}`}
                    className={`knowledge-sync-workspace-card${activeWorkspaceId === profile.id ? " active" : ""}`}
                  >
                    <div className="knowledge-sync-workspace-head">
                      <label className="knowledge-sync-inline-toggle">
                        <input
                          type="radio"
                          name="active-workspace"
                          checked={activeWorkspaceId === profile.id}
                          onChange={() => {
                            setActiveWorkspaceId(profile.id);
                            setLocalTargetWorkspaceId(profile.id);
                          }}
                        />
                        <span>active</span>
                      </label>
                      <label className="knowledge-sync-inline-toggle">
                        <input
                          type="checkbox"
                          checked={selectedWorkspaceIds.includes(profile.id)}
                          onChange={(event) => {
                            setSelectedWorkspaceIds((prev) => {
                              const next = event.target.checked
                                ? normalizeWorkspaceIdList([...prev, profile.id])
                                : prev.filter((workspaceId) => workspaceId !== profile.id);
                              return next.length ? next : [activeWorkspaceId || "root"];
                            });
                          }}
                        />
                        <span>sync</span>
                      </label>
                      <span
                        className={`knowledge-sync-target-status is-${
                          imported ? "connected" : profile.is_root ? "paired" : "saved"
                        }`}
                      >
                        {imported ? "imported" : profile.is_root ? "root" : "local"}
                      </span>
                    </div>
                    <input
                      type="text"
                      value={profile.name}
                      onChange={(event) =>
                        updateWorkspaceProfile(profile.id, { name: event.target.value })
                      }
                      disabled={imported || profile.is_root}
                      placeholder="Workspace name"
                    />
                    <div className="knowledge-sync-workspace-grid">
                      <label className="field-label">
                        <span>Namespace</span>
                        <input
                          type="text"
                          value={profile.namespace || ""}
                          onChange={(event) =>
                            updateWorkspaceProfile(profile.id, {
                              namespace: event.target.value,
                            })
                          }
                          disabled={imported || profile.is_root}
                          placeholder={profile.is_root ? "root merges here" : "work"}
                        />
                      </label>
                      <label className="field-label">
                        <span>Root path</span>
                        <input
                          type="text"
                          value={profile.root_path || ""}
                          onChange={(event) =>
                            updateWorkspaceProfile(profile.id, {
                              root_path: event.target.value,
                            })
                          }
                          disabled={imported}
                          placeholder="data/files/workspace/work"
                        />
                      </label>
                    </div>
                    {imported ? (
                      <div className="status-note">
                        Source: {profile.source_device_name || "remote"} /{" "}
                        {profile.source_workspace_name || profile.name}
                      </div>
                    ) : null}
                    {!profile.is_root ? (
                      <div className="knowledge-sync-target-actions">
                        <button
                          type="button"
                          className="knowledge-sync-target-remove"
                          onClick={() => removeWorkspaceProfile(profile.id)}
                        >
                          Remove
                        </button>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
            <div className="knowledge-sync-workspace-create">
              <input
                type="text"
                value={newWorkspaceName}
                onChange={(event) => setNewWorkspaceName(event.target.value)}
                placeholder="New workspace name"
              />
              <input
                type="text"
                value={newWorkspaceNamespace}
                onChange={(event) => setNewWorkspaceNamespace(event.target.value)}
                placeholder="namespace (optional)"
              />
              <input
                type="text"
                value={newWorkspaceRootPath}
                onChange={(event) => setNewWorkspaceRootPath(event.target.value)}
                placeholder="root path (optional)"
              />
              <button type="button" className="icon-btn" onClick={addWorkspaceProfile}>
                Add workspace
              </button>
            </div>
          </div>
          <dl className="knowledge-sync-meta">
            <div>
              <dt>Hostname</dt>
              <dd>{overview?.current_device?.hostname || "unknown"}</dd>
            </div>
            <div>
              <dt>Paired devices</dt>
              <dd>{deviceCounts?.paired ?? savedPeers.length}</dd>
            </div>
            <div>
              <dt>Connection</dt>
              <dd>{selectedPeerConnectionLabel}</dd>
            </div>
            <div>
              <dt>Active workspace</dt>
              <dd>{workspaceLabel(localWorkspaceProfiles, activeWorkspaceId)}</dd>
            </div>
            <div>
              <dt>Trusted here</dt>
              <dd>{deviceCounts?.trusted ?? inboundDevices.length}</dd>
            </div>
            <div>
              <dt>Pending review</dt>
              <dd>{deviceCounts?.pending_push_reviews ?? pendingReviews.length}</dd>
            </div>
            <div>
              <dt>Legacy records</dt>
              <dd>{deviceCounts?.legacy ?? legacyInboundDevices.length}</dd>
            </div>
          </dl>
          <div className="knowledge-sync-section-head">
            <strong title="LAN visibility can be changed live. Public internet access stays blocked for now.">
              Device visibility
            </strong>
            <span className="status-note">
              You can use any private reachable address here, not just the local home-network one.
            </span>
          </div>
          <div className="knowledge-sync-visibility-grid">
            <label
              className="knowledge-sync-visibility-card"
              title="Allow private-network devices to pair, request sync tokens, and pull or push data from this Float instance."
            >
              <div className="knowledge-sync-visibility-header">
                <div className="knowledge-sync-section-stack">
                  <strong>Visible on LAN</strong>
                  <span className={`knowledge-sync-visibility-badge ${syncVisibleOnLan ? "is-on" : "is-off"}`}>
                    {syncVisibleOnLan ? "on" : "off"}
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={syncVisibleOnLan}
                  onChange={(event) => updateLanVisibility(event.target.checked)}
                  disabled={visibilityBusy === "lan"}
                />
              </div>
              <div className="knowledge-sync-url-stack">
                <span className="knowledge-sync-url-label">LAN URL</span>
                <code>{lanUrl || "Unable to detect a LAN URL from this session."}</code>
              </div>
              <span className="status-note">
                {syncVisibleOnLan
                  ? "This device can accept private-network connections."
                  : "Turn this on before another device connects."}
              </span>
            </label>
            <label
              className="knowledge-sync-visibility-card"
              title="Choose whether paired devices can push changes straight into this Float instance or whether they must wait for approval first."
            >
              <div className="knowledge-sync-visibility-header">
                <div className="knowledge-sync-section-stack">
                  <strong>Auto-accept push</strong>
                  <span className={`knowledge-sync-visibility-badge ${syncAutoAcceptPush ? "is-on" : "is-off"}`}>
                    {syncAutoAcceptPush ? "on" : "review"}
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={syncAutoAcceptPush}
                  onChange={(event) => updatePushReviewMode(event.target.checked)}
                  disabled={visibilityBusy === "push-review"}
                />
              </div>
              <div className="knowledge-sync-url-stack">
                <span className="knowledge-sync-url-label">Push behavior</span>
                <code>{syncAutoAcceptPush ? "Apply immediately" : "Queue for approval"}</code>
              </div>
              <span className="status-note">
                {syncAutoAcceptPush
                  ? "Paired devices can push here without a review step."
                  : "Incoming pushes wait in the review list below until you approve them."}
              </span>
            </label>
            <div
              className="knowledge-sync-visibility-card is-disabled"
              title="Reserved for a future gateway path. Public internet device access is still off."
            >
              <div className="knowledge-sync-visibility-header">
                <div className="knowledge-sync-section-stack">
                  <strong>Visible online</strong>
                  <span className="knowledge-sync-visibility-badge is-disabled">later</span>
                </div>
                <input type="checkbox" checked={false} disabled />
              </div>
              <div className="knowledge-sync-url-stack">
                <span className="knowledge-sync-url-label">Internet URL</span>
                <code>{internetUrl || "Not configured."}</code>
              </div>
              <span className="status-note">Public connections stay off for now.</span>
            </div>
          </div>
          <button type="button" className="icon-btn" onClick={saveDeviceSettings} disabled={savingPrefs}>
            {savingPrefs ? "Saving..." : "Save device settings"}
          </button>
          <div className="knowledge-sync-card-subtle">
            <strong>
              <SyncLabelText
                text="Pairing code"
                tooltip="One-time code for mutual trust setup. Generate it on the device you want to receive sync or stream access."
              />
            </strong>
            <span>
              Generate a one-time code on this device, then enter it from another trusted device.
            </span>
            <button
              type="button"
              className="icon-btn"
              onClick={createPairingOffer}
              disabled={pairBusy || !syncVisibleOnLan}
            >
              {pairBusy ? "Creating..." : "Generate pairing code"}
            </button>
            {!syncVisibleOnLan ? (
              <span className="status-note">Enable LAN visibility before inviting another device.</span>
            ) : null}
            {localPairOffer ? (
              <div className="knowledge-sync-section-stack">
                <strong className="knowledge-sync-offer-code">{localPairOffer.code}</strong>
                <span className="status-note">Expires {formatDateTime(localPairOffer.expires_at)}</span>
              </div>
            ) : null}
          </div>
        </section>

        <section className="knowledge-sync-card">
          <div className="knowledge-sync-section-head">
            <h4>{selectedPeerId ? "Edit paired device" : "Pair a device"}</h4>
            {selectedPeerId ? (
              <button type="button" className="icon-btn" onClick={resetPairEditor}>
                New pair
              </button>
            ) : null}
          </div>
          <label className="field-label" htmlFor="sync-target-name">
            <SyncLabelText
              text="Device label"
              tooltip="Friendly name stored in your paired-device list. This can differ from the remote machine hostname."
            />
          </label>
          <input
            id="sync-target-name"
            type="text"
            value={targetLabel}
            onChange={(event) => setTargetLabel(event.target.value)}
            placeholder="laptop"
          />
          <label className="field-label" htmlFor="sync-target-url">
            <SyncLabelText
              text="Remote Float URL"
              tooltip="Use the other device's private reachable address here. Public internet URLs are intentionally unsupported right now."
            />
          </label>
          <input
            id="sync-target-url"
            type="text"
            value={syncRemoteUrl}
            onChange={(event) => setSyncRemoteUrl(event.target.value)}
            placeholder="http://192.168.1.25:5000"
          />
          {remoteAddressDirty ? (
            <p className="status-note">
              Unsaved address change. Save or pair again to replace the stored URL for this device.
            </p>
          ) : null}
          {syncRemoteUrl.trim() ? (
            <div className="knowledge-sync-card-subtle">
              <div className="knowledge-sync-section-head">
                <strong>Remote status</strong>
                <div className="knowledge-sync-head-actions">
                  <span
                    className={`knowledge-sync-target-status is-${
                      peerStatusBusy ? "pending" : peerStatus?.reachable ? "paired" : peerStatus ? "legacy" : "saved"
                    }`}
                  >
                    {peerStatusBusy
                      ? "checking"
                      : peerStatus?.reachable
                        ? "reachable"
                        : peerStatus
                          ? "unreachable"
                          : "not checked"}
                  </span>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={checkPeerStatus}
                    disabled={peerStatusBusy}
                  >
                    {peerStatusBusy ? "Checking..." : "Check remote"}
                  </button>
                </div>
              </div>
              {peerStatus?.reachable ? (
                <div className="knowledge-sync-target-meta">
                  <span>{peerStatus.display_name || peerStatus.hostname || "Remote device"}</span>
                  {typeof peerStatus.visible_on_lan === "boolean" ? (
                    <span>{peerStatus.visible_on_lan ? "LAN visible" : "LAN hidden"}</span>
                  ) : null}
                  {selectedPeer?.remote_device_id && !remoteAddressDirty ? (
                    <span>{selectedPeerConnected ? "Connected to this saved pair" : "Saved pair is reachable"}</span>
                  ) : null}
                  {peerStatus.advertised_lan_url ? <span>{peerStatus.advertised_lan_url}</span> : null}
                </div>
              ) : peerStatus?.error ? (
                <div className="status-note">{peerStatus.error}</div>
              ) : (
                <div className="status-note">
                  No remote request has been sent yet. Check remote, pair, or preview when you are ready.
                </div>
              )}
            </div>
          ) : null}
          <div className="knowledge-sync-card-subtle">
            <strong title="Choose which local and remote workspaces participate in preview/apply, and whether incoming data should merge into the destination or land as a nested imported workspace.">
              Workspace mapping
            </strong>
            <div className="knowledge-sync-inline-mode-row">
              <label className="knowledge-sync-inline-toggle">
                <input
                  type="radio"
                  checked={workspaceMode === "merge"}
                  onChange={() => setWorkspaceMode("merge")}
                />
                <span>merge</span>
              </label>
              <label className="knowledge-sync-inline-toggle">
                <input
                  type="radio"
                  checked={workspaceMode === "import"}
                  onChange={() => setWorkspaceMode("import")}
                />
                <span>import nested</span>
              </label>
            </div>
            <div className="knowledge-sync-workspace-mapping-grid">
              <div className="knowledge-sync-workspace-picker">
                <span className="knowledge-sync-url-label">Local source workspaces</span>
                <div className="knowledge-sync-workspace-chip-row">
                  {localWorkspaceProfiles.map((profile) => (
                    <label key={`local-source-${profile.id}`} className="knowledge-sync-scope-toggle">
                      <input
                        type="checkbox"
                        checked={selectedWorkspaceIds.includes(profile.id)}
                        onChange={(event) =>
                          setSelectedWorkspaceIds((prev) => {
                            const next = event.target.checked
                              ? normalizeWorkspaceIdList([...prev, profile.id])
                              : prev.filter((workspaceId) => workspaceId !== profile.id);
                            return next.length ? next : [activeWorkspaceId || "root"];
                          })
                        }
                      />
                      <span>{profile.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="knowledge-sync-workspace-picker">
                <span className="knowledge-sync-url-label">Remote source workspaces</span>
                {remoteWorkspaceProfiles.length ? (
                  <div className="knowledge-sync-workspace-chip-row">
                    {remoteWorkspaceProfiles.map((profile) => (
                      <label key={`remote-source-${profile.id}`} className="knowledge-sync-scope-toggle">
                        <input
                          type="checkbox"
                          checked={remoteWorkspaceIds.includes(profile.id)}
                          onChange={(event) =>
                            setRemoteWorkspaceIds((prev) => {
                              const next = event.target.checked
                                ? normalizeWorkspaceIdList([...prev, profile.id])
                                : prev.filter((workspaceId) => workspaceId !== profile.id);
                              return next.length ? next : [profile.id];
                            })
                          }
                        />
                        <span>{profile.name}</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <span className="status-note">Remote workspaces appear after the URL resolves.</span>
                )}
              </div>
            </div>
            <div className="knowledge-sync-workspace-mapping-grid">
              <label className="field-label">
                <span>Pull target workspace</span>
                <select
                  value={localTargetWorkspaceId}
                  onChange={(event) => setLocalTargetWorkspaceId(event.target.value)}
                >
                  {localWorkspaceProfiles.map((profile) => (
                    <option key={`local-target-${profile.id}`} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                <span>Push target workspace</span>
                <select
                  value={remoteTargetWorkspaceId}
                  onChange={(event) => setRemoteTargetWorkspaceId(event.target.value)}
                >
                  {remoteWorkspaceProfiles.length ? (
                    remoteWorkspaceProfiles.map((profile) => (
                      <option key={`remote-target-${profile.id}`} value={profile.id}>
                        {profile.name}
                      </option>
                    ))
                  ) : (
                    <option value="root">Remote workspace</option>
                  )}
                </select>
              </label>
            </div>
            {importModeInvalid ? (
              <div className="status-note">
                Import mode currently supports one local and one remote source workspace at a time.
              </div>
            ) : null}
          </div>
          <div className="knowledge-sync-scope-row">
            {DEVICE_SCOPE_OPTIONS.map((scope) => (
              <label key={scope} className="knowledge-sync-scope-toggle">
                <input
                  type="checkbox"
                  checked={targetScopes.includes(scope)}
                  onChange={() =>
                    setTargetScopes((prev) =>
                      normalizePeerScopes(
                        prev.includes(scope) ? prev.filter((item) => item !== scope) : [...prev, scope],
                      ),
                    )
                  }
                />
                <span>{scope}</span>
              </label>
            ))}
          </div>
          <label className="field-label" htmlFor="sync-target-code">
            <SyncLabelText
              text="Pairing code"
              tooltip="Paste the one-time code shown on the other device. Pairing establishes trust; preview and apply decide what actually syncs."
            />
          </label>
          <input
            id="sync-target-code"
            type="text"
            value={pairCodeInput}
            onChange={(event) => setPairCodeInput(event.target.value.toUpperCase())}
            placeholder="ABCD1234"
          />
          <div className="knowledge-sync-actions">
            <button type="button" className="icon-btn" onClick={upsertSavedPeer} disabled={savingPrefs}>
              {savingPrefs
                ? "Saving..."
                : selectedPeerId
                  ? remoteAddressDirty
                    ? "Replace saved URL"
                    : "Update pair"
                  : "Save pair"}
            </button>
            <button type="button" className="icon-btn" onClick={pairWithCode} disabled={pairBusy}>
              {pairBusy ? "Pairing..." : "Pair now"}
            </button>
            <button type="button" className="icon-btn" onClick={previewSync} disabled={syncBusy || syncActionBusy}>
              {syncBusy ? "Checking..." : "Preview sync"}
            </button>
          </div>
          <p className="status-note">
            Use a private reachable address here. Public internet sync stays off for now.
          </p>
        </section>
      </div>

      <section className="knowledge-sync-card">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Paired devices"
              tooltip="Saved trusted targets. Each pair remembers the remote base URL, device id, and allowed scopes."
            />
          </h4>
          <p className="status-note">Named targets with stable device ids, scopes, and last-used details.</p>
        </div>
        {savedPeers.length ? (
          <div className="knowledge-sync-target-list">
            {savedPeers.map((peer) => {
              const peerReachable =
                peer.id === selectedPeerId &&
                !remoteAddressDirty &&
                !!peerStatus?.reachable &&
                syncRemoteUrl.trim() === String(peer.remote_url || "").trim();
              const peerState = describePeerStatus(peer, { reachable: peerReachable });
              return (
                <article
                  key={peer.id}
                  className={`knowledge-sync-target-card ${peer.id === selectedPeerId ? "active" : ""}`}
                >
                  <button
                    type="button"
                    className="knowledge-sync-target-main"
                    onClick={() => {
                      setSelectedPeerId(peer.id);
                      setTargetLabel(peer.label);
                      setTargetScopes(normalizePeerScopes(peer.scopes));
                      setSyncRemoteUrl(peer.remote_url);
                      setSelectedWorkspaceIds(
                        normalizeWorkspaceIdList(peer.local_workspace_ids).length
                          ? normalizeWorkspaceIdList(peer.local_workspace_ids)
                          : [activeWorkspaceId || "root"],
                      );
                      setRemoteWorkspaceIds(normalizeWorkspaceIdList(peer.remote_workspace_ids));
                      setWorkspaceMode(peer.workspace_mode || "merge");
                      setLocalTargetWorkspaceId(
                        peer.local_target_workspace_id || activeWorkspaceId || "root",
                      );
                      setRemoteTargetWorkspaceId(peer.remote_target_workspace_id || "root");
                      setSyncPreview(null);
                      setMessage("");
                    }}
                  >
                    <div className="knowledge-sync-target-title-row">
                      <strong>{peer.label}</strong>
                      <span className={`knowledge-sync-target-status is-${peerState.key}`}>
                        {peerState.label}
                      </span>
                    </div>
                    <span>{peer.remote_url}</span>
                    <div className="knowledge-sync-preview-chip-row">
                      {normalizePeerScopes(peer.scopes).map((scope) => (
                        <span key={`${peer.id}-${scope}`} className="knowledge-sync-preview-chip">
                          <strong>scope</strong>
                          <span>{scope}</span>
                        </span>
                      ))}
                      {peer.remote_device_name ? (
                        <span className="knowledge-sync-preview-chip">
                          <strong>remote</strong>
                          <span>{peer.remote_device_name}</span>
                        </span>
                      ) : null}
                      <span className="knowledge-sync-preview-chip">
                        <strong>mode</strong>
                        <span>{peer.workspace_mode || "merge"}</span>
                      </span>
                    </div>
                    <div className="knowledge-sync-target-meta">
                      <span>{peerReachable ? "Connected now" : `Last used ${formatDateTime(peer.last_used_at)}`}</span>
                      {peer.remote_device_id ? <span>remote id {peer.remote_device_id.slice(0, 8)}</span> : null}
                      {peer.local_workspace_ids?.length ? (
                        <span>
                          local {peer.local_workspace_ids.map((id) => workspaceLabel(localWorkspaceProfiles, id)).join(", ")}
                        </span>
                      ) : null}
                      {peer.remote_workspace_ids?.length ? (
                        <span>remote {peer.remote_workspace_ids.join(", ")}</span>
                      ) : null}
                    </div>
                  </button>
                  <div className="knowledge-sync-target-actions">
                    <button type="button" className="knowledge-sync-target-remove" onClick={() => syncPairTrust(peer)}>
                      {pairSyncBusy && selectedPeerId === peer.id ? "Updating..." : "Refresh trust"}
                    </button>
                    <button type="button" className="knowledge-sync-target-remove" onClick={() => revokeRemotePair(peer)}>
                      Revoke remote
                    </button>
                    <button type="button" className="knowledge-sync-target-remove" onClick={() => removeSavedPeer(peer)}>
                      Remove
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <p className="status-note">No paired devices yet. Save one once you know the remote URL.</p>
        )}
      </section>

      {syncPreview ? (
        <section className="knowledge-sync-card">
          <div className="knowledge-sync-section-head">
            <h4>
              <SyncLabelText
                text="Sync preview"
                tooltip="Compare local and remote manifests before applying a pull or push. This is where you choose which sections actually move."
              />
            </h4>
            <p className="status-note">
              Compare this device with {syncPreview?.remote?.base_url || syncRemoteUrl.trim()} and
              choose which sections to merge.
            </p>
          </div>
          <div className="knowledge-sync-card-subtle">
            <strong>Remote device</strong>
            <span>{syncPreview?.remote?.hostname || syncPreview?.remote?.base_url || "Unknown device"}</span>
          </div>
          <div className="knowledge-sync-target-meta">
            <span>
              Pull target {workspaceLabel(localWorkspaceProfiles, syncPreview?.workspaces?.local?.target_workspace_id || localTargetWorkspaceId)}
            </span>
            <span>
              Push target {workspaceLabel(remoteWorkspaceProfiles, syncPreview?.workspaces?.remote?.target_workspace_id || remoteTargetWorkspaceId)}
            </span>
            <span>Mode {syncPreview?.workspace_mode || workspaceMode}</span>
          </div>
          {Array.isArray(syncPreview?.workspaces?.local?.ignored_workspace_ids) &&
          syncPreview.workspaces.local.ignored_workspace_ids.length ? (
            <div className="status-note">
              Ignored local workspaces to avoid recursive sync:{" "}
              {syncPreview.workspaces.local.ignored_workspace_ids
                .map((workspaceId) => workspaceLabel(localWorkspaceProfiles, workspaceId))
                .join(", ")}
            </div>
          ) : null}
          {syncPreview?.link_to_source ? (
            <div className="knowledge-sync-namespace-notes">
              <p className="status-note">
                Pull here will store incoming data under{" "}
                <code>{syncPreview?.effective_namespaces?.pull || "remote"}/</code>.
              </p>
              <p className="status-note">
                Push there will store this device under{" "}
                <code>{syncPreview?.effective_namespaces?.push || "this-device"}/</code>.
              </p>
            </div>
          ) : null}
          <div className="knowledge-sync-preview-list">
            {pullSections.map((section) => {
              const pushSection = pushSectionMap[section.key] || section;
              const diffItems = syncSectionDiffItems(section);
              const pullItems = syncSectionActionableItems(section, "pull");
              const pushItems = syncSectionActionableItems(pushSection, "push");
              const pullSelectedCount = syncSectionSelectedCount(
                syncItemSelections,
                "pull",
                section.key,
              );
              const pushSelectedCount = syncSectionSelectedCount(
                syncItemSelections,
                "push",
                section.key,
              );
              return (
                <label key={section.key} className="knowledge-sync-preview-card">
                  <input
                    type="checkbox"
                    checked={!!syncSelections[section.key]}
                    onChange={(event) =>
                      setSyncSelections((prev) => ({ ...prev, [section.key]: event.target.checked }))
                    }
                  />
                  <div>
                    <strong>{section.label}</strong>
                    <div className="status-note">
                      Pull here: {describeSyncDirectionSummary(section, "pull", remotePreviewLabel)}
                    </div>
                    <div className="status-note">
                      Push there: {describeSyncDirectionSummary(pushSection, "push", remotePreviewLabel)}
                    </div>
                    <div className="knowledge-sync-target-meta">
                      <span>{diffItems.length} differing items</span>
                      <span>Pull selected {pullSelectedCount}/{pullItems.length}</span>
                      <span>Push selected {pushSelectedCount}/{pushItems.length}</span>
                    </div>
                    <div className="knowledge-sync-actions knowledge-sync-preview-actions">
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          setSyncItemReview({ direction: "pull", sectionKey: section.key });
                        }}
                        disabled={!pullItems.length}
                      >
                        {pullItems.length
                          ? `Review pull items (${pullSelectedCount}/${pullItems.length})`
                          : "Nothing to pull"}
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          setSyncItemReview({ direction: "push", sectionKey: section.key });
                        }}
                        disabled={!pushItems.length}
                      >
                        {pushItems.length
                          ? `Review push items (${pushSelectedCount}/${pushItems.length})`
                          : "Nothing to push"}
                      </button>
                    </div>
                    <div className="knowledge-sync-preview-chip-row">
                      {diffItems.slice(0, 8).map((item) => (
                        <span key={`${section.key}-${item.resource_id}-${item.status}`} className="knowledge-sync-preview-chip">
                          <strong>{item.label || item.resource_id}</strong>
                          <span>{syncPreviewStatusLabel(item.status)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
          <div className="knowledge-sync-actions">
            <button type="button" className="icon-btn" onClick={() => applySync("pull")} disabled={!!syncActionBusy}>
              {syncActionBusy === "pull" ? "Pulling..." : "Pull here"}
            </button>
            <button type="button" className="icon-btn" onClick={() => applySync("push")} disabled={!!syncActionBusy}>
              {syncActionBusy === "push" ? "Pushing..." : "Push there"}
            </button>
          </div>
        </section>
      ) : null}

      {syncItemReviewSection ? (
        <div
          className="knowledge-sync-review-modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setSyncItemReview(null);
            }
          }}
        >
          <div
            className="knowledge-sync-review-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Review ${syncItemReviewDirection} items for ${syncItemReviewSection.label}`}
          >
            <div className="knowledge-sync-section-head">
              <div className="knowledge-sync-section-stack">
                <strong>
                  {syncItemReviewSection.label} · {syncItemReviewDirection === "push" ? "push" : "pull"} review
                </strong>
                <span className="status-note">
                  {syncItemReviewDirection === "push"
                    ? `Choose which ${syncItemReviewSection.label.toLowerCase()} to send from this device.`
                    : `Choose which ${syncItemReviewSection.label.toLowerCase()} to pull from ${remotePreviewLabel}.`}
                </span>
              </div>
              <button type="button" className="icon-btn" onClick={() => setSyncItemReview(null)}>
                Close
              </button>
            </div>
            <div className="knowledge-sync-target-meta">
              <span>
                Selected {syncItemReviewSelectedIds.length}/{syncItemReviewItems.length}
              </span>
              <span>{syncItemReviewItems.length} actionable items</span>
            </div>
            <div className="knowledge-sync-actions">
              <button
                type="button"
                className="icon-btn"
                onClick={() =>
                  updateSyncItemSelection(
                    syncItemReviewDirection,
                    syncItemReviewSectionKey,
                    syncItemReviewItems.map((item) => item?.selection_id || item?.resource_id),
                  )
                }
                disabled={!syncItemReviewItems.length || syncItemReviewAllSelected}
              >
                Select all
              </button>
              <button
                type="button"
                className="icon-btn"
                onClick={() => updateSyncItemSelection(syncItemReviewDirection, syncItemReviewSectionKey, [])}
                disabled={!syncItemReviewSelectedIds.length}
              >
                Clear
              </button>
            </div>
            <div className="knowledge-sync-review-modal-list">
              {syncItemReviewItems.map((item) => {
                const selectionId = String(item?.selection_id || item?.resource_id || "").trim();
                const selected = syncItemReviewSelectedIds.includes(selectionId);
                return (
                  <label
                    key={`${syncItemReviewDirection}-${syncItemReviewSectionKey}-${selectionId}`}
                    className="knowledge-sync-review-item"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        toggleSyncReviewItem(syncItemReviewDirection, syncItemReviewSectionKey, selectionId)
                      }
                    />
                    <div className="knowledge-sync-section-stack">
                      <strong>{item?.label || item?.resource_id}</strong>
                      {item?.detail ? <span className="status-note">{item.detail}</span> : null}
                      {describeSyncItemTiming(item) ? (
                        <span className="status-note">{describeSyncItemTiming(item)}</span>
                      ) : null}
                    </div>
                    <span className="knowledge-sync-preview-item-status">
                      {syncPreviewStatusLabel(item?.status)}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      <section className="knowledge-sync-card">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Recent sync activity"
              tooltip="Local pulls and approved incoming syncs are snapshot-based actions. Undo restores the pre-sync snapshot when no newer conflicting changes exist."
            />
          </h4>
          <p className="status-note">
            Undo is local only. It restores this device to the snapshot from before that sync ran.
          </p>
        </div>
        {recentSyncActions.length ? (
          <div className="knowledge-sync-review-list compact">
            {recentSyncActions.map((action) => {
              const status = describeSyncHistoryStatus(action);
              const actionSections = Array.isArray(action?.batch_scope?.sections)
                ? action.batch_scope.sections
                : [];
              return (
                <article key={`sync-history-${action.id}`} className="knowledge-sync-review-card compact">
                  <div className="knowledge-sync-section-head">
                    <div className="knowledge-sync-section-stack">
                      <strong>{action.summary || "Sync activity"}</strong>
                      <span className="status-note">{formatDateTime(action.created_at || action.created_at_ts)}</span>
                    </div>
                    <span className={`knowledge-sync-target-status is-${status.key}`}>{status.label}</span>
                  </div>
                  <div className="knowledge-sync-target-meta">
                    <span>{action.item_count || 0} changed items</span>
                    {action?.batch_scope?.remote ? <span>{action.batch_scope.remote}</span> : null}
                    {action.reverted_at ? <span>undone {formatDateTime(action.reverted_at)}</span> : null}
                  </div>
                  {actionSections.length ? (
                    <div className="knowledge-sync-preview-chip-row">
                      {actionSections.map((section) => (
                        <span key={`${action.id}-${section}`} className="knowledge-sync-preview-chip">
                          <strong>section</strong>
                          <span>{section}</span>
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {action.revertible && !action.reverted_at ? (
                    <div className="knowledge-sync-actions">
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => revertSyncAction(action)}
                        disabled={undoSyncBusyId === action.id}
                      >
                        {undoSyncBusyId === action.id ? "Undoing..." : "Undo"}
                      </button>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="status-note">No recent local sync history is available yet.</p>
        )}
      </section>

      <section className="knowledge-sync-card">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Push review"
              tooltip="Incoming pushes from paired devices appear here when auto-accept is off. Approve to apply them, or reject to drop them."
            />
          </h4>
          <p className="status-note">
            {syncAutoAcceptPush
              ? "Auto-accept is on. Paired devices can push here immediately."
              : "Auto-accept is off. Incoming pushes wait here for review."}
          </p>
        </div>
        {pendingReviews.length ? (
          <div className="knowledge-sync-review-list">
            {pendingReviews.map((review) => (
              <article key={review.id} className="knowledge-sync-review-card">
                <div className="knowledge-sync-section-head">
                  <div className="knowledge-sync-section-stack">
                    <strong>{review.source_label || "Remote device"}</strong>
                    <span className="knowledge-sync-device-id">
                      queued {formatDateTime(review.created_at)}
                    </span>
                  </div>
                  <span className="knowledge-sync-target-status is-pending">pending review</span>
                </div>
                <div className="knowledge-sync-preview-chip-row">
                  {(review.requested_section_labels || review.requested_sections || []).map((section) => (
                    <span key={`${review.id}-${section}`} className="knowledge-sync-preview-chip">
                      <strong>section</strong>
                      <span>{section}</span>
                    </span>
                  ))}
                </div>
                <div className="knowledge-sync-target-meta">
                  {review.device_name ? <span>device {review.device_name}</span> : null}
                  {review.device_id ? <span>id {review.device_id.slice(0, 8)}</span> : null}
                </div>
                <div className="knowledge-sync-actions">
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => approvePendingReview(review)}
                    disabled={reviewBusyId === review.id}
                  >
                    {reviewBusyId === review.id ? "Applying..." : "Approve"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => rejectPendingReview(review)}
                    disabled={reviewBusyId === review.id}
                  >
                    Reject
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="status-note">
            {syncAutoAcceptPush
              ? "No pending reviews because pushes apply immediately."
              : "No pending push reviews right now."}
          </p>
        )}
        {recentReviews.length ? (
          <div className="knowledge-sync-review-history">
            <strong>Recent sync decisions</strong>
            <div className="knowledge-sync-review-list compact">
              {recentReviews.map((review) => (
                <article key={`recent-${review.id}`} className="knowledge-sync-review-card compact">
                  <div className="knowledge-sync-section-head">
                    <strong>{review.source_label || "Remote device"}</strong>
                    <span className={`knowledge-sync-target-status is-${review.status || "reviewed"}`}>
                      {review.status || "reviewed"}
                    </span>
                  </div>
                  <div className="knowledge-sync-target-meta">
                    <span>{summarizeRequestedSections(review.requested_section_labels || review.requested_sections)}</span>
                    <span>{formatDateTime(review.updated_at || review.created_at)}</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="knowledge-sync-card">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Trusted devices on this instance"
              tooltip="Inbound trust records stored here. Revoking one removes its local ability to request sync or stream access from this device."
            />
          </h4>
          <p className="status-note">These are device records registered here. Revoke them to remove local trust.</p>
        </div>
        {inboundDevices.length ? (
          <div className="knowledge-sync-device-list">
            {inboundDevices.map((device) => (
              <article key={device.id} className="knowledge-sync-device-card">
                <div className="knowledge-sync-section-head">
                  <div className="knowledge-sync-section-stack">
                    <div className="knowledge-sync-target-title-row">
                      <strong>{device.name}</strong>
                      <span className={`knowledge-sync-target-status is-${device.status || "trusted"}`}>
                        {device.status_label || "Trusted device"}
                      </span>
                    </div>
                    <span className="knowledge-sync-device-id">id {device.id.slice(0, 8)}</span>
                  </div>
                  <button type="button" className="icon-btn" onClick={() => revokeInboundDevice(device)}>
                    Revoke
                  </button>
                </div>
                <div className="knowledge-sync-preview-chip-row">
                  {Array.isArray(device.capabilities?.requested_scopes) && device.capabilities.requested_scopes.length ? (
                    device.capabilities.requested_scopes.map((scope) => (
                      <span key={`${device.id}-${scope}`} className="knowledge-sync-preview-chip">
                        <strong>scope</strong>
                        <span>{scope}</span>
                      </span>
                    ))
                  ) : (
                    <span className="knowledge-sync-preview-chip">
                      <strong>scope</strong>
                      <span>unspecified</span>
                    </span>
                  )}
                </div>
                <div className="status-note">Last seen {formatDateTime(device.last_seen)}</div>
                <div className="status-note">Created {formatDateTime(device.created_at)}</div>
              </article>
            ))}
          </div>
        ) : (
          <p className="status-note">No inbound trusted devices recorded yet.</p>
        )}
        {legacyInboundDevices.length ? (
          <div className="knowledge-sync-card-subtle">
            <div className="knowledge-sync-section-head">
              <div className="knowledge-sync-section-stack">
                <strong>Legacy browser records</strong>
                <span className="status-note">
                  Older browser-origin entries without sync scopes. These are usually stale local test records.
                </span>
              </div>
              <button
                type="button"
                className="icon-btn"
                onClick={pruneLegacyDevices}
                disabled={pruneLegacyBusy}
              >
                {pruneLegacyBusy ? "Cleaning..." : `Prune ${legacyInboundDevices.length}`}
              </button>
            </div>
            <div className="knowledge-sync-device-list compact">
              {legacyInboundDevices.slice(0, 6).map((device) => (
                <article key={`legacy-${device.id}`} className="knowledge-sync-device-card compact">
                  <div className="knowledge-sync-target-title-row">
                    <strong>{device.name}</strong>
                    <span className="knowledge-sync-target-status is-legacy">legacy</span>
                  </div>
                  <div className="knowledge-sync-target-meta">
                    <span>created {formatDateTime(device.created_at)}</span>
                    <span>last seen {formatDateTime(device.last_seen)}</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="knowledge-sync-card">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Import and export"
              tooltip="Local file movement for conversations and knowledge archives. This is separate from trusted-device sync."
            />
          </h4>
          <p className="status-note">Import archives into a folder path, or export the full conversation set here.</p>
        </div>
        <input
          ref={importFileInputRef}
          type="file"
          className="knowledge-sync-hidden-input"
          accept=".zip,.json,.md,.txt"
          onChange={handleImportFileChange}
        />
        <div className="knowledge-sync-grid">
          <div className="knowledge-sync-card-subtle">
            <strong>Import</strong>
            <span>Preview archive contents, choose files, then import into root or a folder path.</span>
            <button type="button" className="icon-btn" onClick={triggerImportPicker} disabled={importBusy}>
              {importBusy ? "Working..." : "Import file or archive"}
            </button>
            {importStatus ? <span className="status-note">{importStatus}</span> : null}
          </div>
          <div className="knowledge-sync-card-subtle">
            <strong>Export all</strong>
            <label className="field-label" htmlFor="sync-export-format">
              <span>Format</span>
            </label>
            <select
              id="sync-export-format"
              value={exportDefaults.format}
              onChange={(event) =>
                setExportDefaults((prev) => ({ ...prev, format: normalizeExportFormat(event.target.value) }))
              }
            >
              <option value="md">Markdown</option>
              <option value="json">JSON</option>
              <option value="text">Text</option>
            </select>
            <div className="knowledge-sync-scope-row">
              {[
                ["includeChat", "Chat"],
                ["includeThoughts", "Thoughts"],
                ["includeTools", "Tools"],
              ].map(([key, label]) => (
                <label key={key} className="knowledge-sync-scope-toggle">
                  <input
                    type="checkbox"
                    checked={!!exportDefaults[key]}
                    onChange={(event) =>
                      setExportDefaults((prev) => ({ ...prev, [key]: event.target.checked }))
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <button type="button" className="icon-btn" onClick={handleExportAll} disabled={exportBusy}>
              {exportBusy ? "Exporting..." : "Export all"}
            </button>
          </div>
        </div>
        {importReview.file ? (
          <div className="knowledge-sync-import-review">
            <div className="knowledge-sync-section-head">
              <div className="knowledge-sync-section-stack">
                <strong>Import review</strong>
                <span className="status-note">{importReview.file.name}</span>
              </div>
              <button type="button" className="icon-btn" onClick={clearImportReview}>
                Clear
              </button>
            </div>
            <label className="field-label" htmlFor="sync-import-folder">
              <span>Destination folder</span>
            </label>
            <input
              id="sync-import-folder"
              type="text"
              value={importReview.destinationFolder}
              onChange={(event) => setImportReview((prev) => ({ ...prev, destinationFolder: event.target.value }))}
              placeholder="Leave blank for root"
            />
            <div className="knowledge-sync-section-head">
              <span className="status-note">
                Detected files ({importReviewSelectedCount}/{importReview.detectedFiles.length})
              </span>
              <button
                type="button"
                className="icon-btn"
                onClick={() => {
                  const nextSelected = {};
                  importReview.detectedFiles.forEach((item) => {
                    const path = String(item?.path || item?.name || "").trim();
                    if (path) nextSelected[path] = !importReviewAllSelected;
                  });
                  setImportReview((prev) => ({ ...prev, selectedFiles: nextSelected }));
                }}
              >
                {importReviewAllSelected ? "Deselect all" : "Select all"}
              </button>
            </div>
            <div className="knowledge-sync-import-list">
              {importReview.detectedFiles.map((item) => {
                const path = String(item?.path || item?.name || "").trim();
                if (!path) return null;
                return (
                  <label key={`import-file-${path}`} className="knowledge-sync-import-item">
                    <input
                      type="checkbox"
                      checked={Boolean(importReview.selectedFiles[path])}
                      onChange={() =>
                        setImportReview((prev) => ({
                          ...prev,
                          selectedFiles: { ...prev.selectedFiles, [path]: !prev.selectedFiles[path] },
                        }))
                      }
                    />
                    <div className="knowledge-sync-section-stack">
                      <strong>{path}</strong>
                      <span className="status-note">{item.message_count ?? 0} messages</span>
                    </div>
                  </label>
                );
              })}
            </div>
            <button
              type="button"
              className="icon-btn"
              onClick={() =>
                uploadConversationImport({
                  file: importReview.file,
                  selectedFiles: Object.entries(importReview.selectedFiles)
                    .filter(([, value]) => Boolean(value))
                    .map(([path]) => path),
                  destinationFolder: importReview.destinationFolder.trim(),
                })
              }
              disabled={importReviewSelectedCount === 0 || importBusy}
            >
              {importBusy ? "Importing..." : "Import selected"}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
};

export default KnowledgeSyncTab;
