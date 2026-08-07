import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import StateInspector from "./StateInspector";
import "../styles/ProgressBar.css";
import "../styles/ImportReview.css";
import { buildSyncOwnershipInspectorRows } from "../utils/stateExplanations";
import {
  WORKSPACE_PRIVATE_PATTERNS_HELP,
  WORKSPACE_PRIVACY_OPTIONS,
  getWorkspacePrivacyTooltip,
  normalizeWorkspacePrivacyMode,
  normalizeWorkspacePrivatePatterns,
  workspacePrivatePatternsText,
  workspaceSyncBlocked,
} from "../utils/privacyLevels";
import {
  describeClassifiedImport,
  isMarkdownOrTextImport,
  normalizeClassifiedImportPreview,
} from "../utils/importClassification";

const DEFAULT_EXPORT_DEFAULTS = {
  format: "md",
  includeChat: true,
  includeThoughts: true,
  includeTools: true,
};

const DEVICE_SCOPE_OPTIONS = ["sync", "stream", "files"];
const DEVICE_SCOPE_HELP = {
  sync: "Allows manifest comparison and selected pull or push of conversations, knowledge, settings, calendars, and attachments.",
  stream: "Allows temporary live session signaling between paired devices. It does not copy stored data by itself.",
  files: "Allows file and blob upload or download for attachments and legacy transfer paths.",
};
const SCOPE_ROW_HELP =
  "Allowed scopes control what this paired device can do. Sync moves data, stream handles live signaling, and files handles blob transfer.";
const REMOTE_URL_HELP =
  "Use the other Float instance's reachable URL. The host or port can change; Float treats the saved device fingerprint as identity and the URL as a reachability hint.";
const LAN_VISIBILITY_HELP =
  "Allow private-network devices to pair and sync, and change Float's real backend listener. Launcher-managed sessions restart the backend on the same port to apply this safely; public internet sync remains unsupported.";
const MOBILE_FLOAT_HELP =
  "Expose the current Float dev server over Tailscale Serve for tailnet devices.";
const ONLINE_VISIBILITY_HELP =
  "Reserved for a future gateway path. Public internet sync is not supported yet. If you need https://, the remote or tunnel must already terminate TLS.";
const PAIRING_CODE_HELP =
  "One-time code for mutual trust setup. Generate it on the device you want to receive sync or stream access.";
const WORKSPACE_MAPPING_HELP =
  "Choose which local and remote workspaces participate in preview and apply. Merge writes into the selected destination. Import nested creates a nested imported workspace instead. Broader workspace switching is still planned.";
const REFRESH_TRUST_HELP =
  "Re-fetch the remote trust record and allowed scopes from the paired device.";
const REVOKE_REMOTE_HELP =
  "Remove the trust record on the remote device and delete the saved pair here.";
const CHECK_REMOTE_HELP =
  "Probe the URL you entered. If this is a saved pair at a new port, Float updates the saved URL only after the remote fingerprint matches.";
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
  let root = {
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
    lineage_id: "",
    origin_deployment_id: "",
    upstream_deployment_id: "",
    sync_back: {},
    privacy_mode: "default",
    private_patterns: [],
  };
  if (Array.isArray(value)) {
    const rootEntry = value.find(
      (entry) => entry && typeof entry === "object" && String(entry.id || "").trim() === "root",
    );
    if (rootEntry) {
      root = {
        ...root,
        name:
          typeof rootEntry.name === "string" && rootEntry.name.trim()
            ? rootEntry.name.trim()
            : root.name,
        privacy_mode: normalizeWorkspacePrivacyMode(rootEntry.privacy_mode),
        private_patterns: normalizeWorkspacePrivatePatterns(rootEntry.private_patterns),
        lineage_id:
          typeof rootEntry.lineage_id === "string" ? rootEntry.lineage_id.trim() : "",
        origin_deployment_id:
          typeof rootEntry.origin_deployment_id === "string"
            ? rootEntry.origin_deployment_id.trim()
            : "",
        upstream_deployment_id:
          typeof rootEntry.upstream_deployment_id === "string"
            ? rootEntry.upstream_deployment_id.trim()
            : "",
        sync_back:
          rootEntry.sync_back && typeof rootEntry.sync_back === "object"
            ? rootEntry.sync_back
            : {},
      };
    }
  }
  const seen = new Set(["root"]);
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
      lineage_id:
        typeof entry.lineage_id === "string" ? entry.lineage_id.trim() : "",
      origin_deployment_id:
        typeof entry.origin_deployment_id === "string"
          ? entry.origin_deployment_id.trim()
          : "",
      upstream_deployment_id:
        typeof entry.upstream_deployment_id === "string"
          ? entry.upstream_deployment_id.trim()
          : "",
      sync_back:
        entry.sync_back && typeof entry.sync_back === "object" ? entry.sync_back : {},
      privacy_mode: normalizeWorkspacePrivacyMode(entry.privacy_mode),
      private_patterns: normalizeWorkspacePrivatePatterns(entry.private_patterns),
    });
  });
  return profiles;
};

const workspaceById = (profiles, workspaceId) =>
  coerceWorkspaceProfiles(profiles).find((profile) => profile.id === workspaceId) || null;

const workspaceLabel = (profiles, workspaceId) =>
  workspaceById(profiles, workspaceId)?.name || workspaceId || "workspace";

const workspacePrivacyLabel = (profile) =>
  normalizeWorkspacePrivacyMode(profile?.privacy_mode);

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
          remote_deployment_id:
            typeof entry.remote_deployment_id === "string" ? entry.remote_deployment_id.trim() : "",
          remote_software:
            entry.remote_software && typeof entry.remote_software === "object"
              ? entry.remote_software
              : {},
          remote_data:
            entry.remote_data && typeof entry.remote_data === "object"
              ? entry.remote_data
              : {},
          data_checkpoint:
            entry.data_checkpoint && typeof entry.data_checkpoint === "object"
              ? entry.data_checkpoint
              : {},
          last_status_at:
            typeof entry.last_status_at === "string" ? entry.last_status_at.trim() : "",
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

const extractSyncError = (error, fallback, { remote = false } = {}) => {
  const rawDetail = error?.response?.data?.detail || error?.message;
  const detail = typeof rawDetail === "string" ? rawDetail.trim() : "";
  if (!detail) return fallback;
  if (
    /HTTP(?:S)?ConnectionPool|NameResolutionError|Max retries exceeded|getaddrinfo failed|ECONNREFUSED|ENOTFOUND|Network Error/i.test(
      detail,
    )
  ) {
    return remote
      ? `${fallback} Check the remote address and make sure the other Float instance is running and reachable.`
      : `${fallback} Check that this Float instance is running and reachable.`;
  }
  return detail;
};

const isSyncRequestCancelled = (error, controller) => {
  const abortedReason = controller?.signal?.aborted ? controller.signal.reason : null;
  if (abortedReason === "user_cancelled" || abortedReason === "component_unmounted") {
    return true;
  }
  return error?.code === "ERR_CANCELED" || error?.name === "CanceledError";
};

const createSyncOperationId = (kind) => {
  const prefix = String(kind || "sync").trim().toLowerCase() || "sync";
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
};

const syncOperationKindLabel = (kind) => {
  const key = String(kind || "").trim().toLowerCase();
  if (key === "check") return "Check remote";
  if (key === "preview") return "Preview";
  if (key === "pull") return "Pull";
  if (key === "push") return "Push";
  return key ? key.replace(/_/g, " ") : "Sync";
};

const syncOperationStatusLabel = (status) => {
  const key = String(status || "").trim().toLowerCase();
  if (key === "cancel_requested") return "Cancel requested";
  if (key === "cancelled") return "Cancelled";
  if (key === "completed") return "Completed";
  if (key === "failed") return "Failed";
  if (key === "running") return "Running";
  return key ? key.replace(/_/g, " ") : "Unknown";
};

const describeSyncOperation = (operation) => {
  if (!operation || typeof operation !== "object") return "None";
  const parts = [
    syncOperationKindLabel(operation.kind),
    syncOperationStatusLabel(operation.status),
  ];
  const remote =
    String(operation.remote_label || "").trim() ||
    (String(operation.remote_url || "").trim() ? "remote device" : "");
  if (remote) parts.push(remote);
  if (operation.started_at || operation.finished_at) {
    parts.push(formatDateTime(operation.finished_at || operation.started_at));
  }
  if (operation.cancel_requested) parts.push("cancel requested");
  return parts.filter(Boolean).join(" - ");
};

const syncSuggestionTone = (severity) => {
  const key = String(severity || "").trim().toLowerCase();
  if (["warning", "pending", "needs_check"].includes(key)) return "pending";
  if (["blocked", "error", "danger"].includes(key)) return "legacy";
  if (["success", "ready"].includes(key)) return "paired";
  return "default";
};

const syncSuggestionSeverityLabel = (severity) => {
  const key = String(severity || "").trim().toLowerCase();
  if (key === "warning") return "needs review";
  if (key === "blocked" || key === "error" || key === "danger") return "blocked";
  if (key === "success" || key === "ready") return "ready";
  return "suggestion";
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
  if (key === "remote_new") return "New remotely since sync";
  if (key === "local_new") return "New here since sync";
  if (key === "remote_deleted") return "Deleted remotely since sync";
  if (key === "local_deleted") return "Deleted here since sync";
  if (key === "remote_newer") return "Remote changed";
  if (key === "local_newer") return "Local changed";
  if (key === "conflict") return "Both changed";
  if (key === "delete_conflict") return "Edit/delete conflict";
  if (key === "known_difference") return "Known difference";
  if (key === "identical") return "Identical";
  return key || "Changed";
};

const SYNC_ACTIONABLE_STATUSES = {
  pull: new Set([
    "only_remote",
    "remote_new",
    "remote_newer",
    "only_local",
    "local_new",
    "local_deleted",
    "remote_deleted",
    "known_difference",
  ]),
  push: new Set([
    "only_local",
    "local_new",
    "local_newer",
    "only_remote",
    "remote_new",
    "local_deleted",
    "remote_deleted",
    "known_difference",
  ]),
};

const syncItemIsDeletion = (item, direction) => {
  const status = String(item?.status || "").trim().toLowerCase();
  if (status === "known_difference") {
    return direction === "pull"
      ? item?.local_present === true && item?.remote_present !== true
      : item?.remote_present === true && item?.local_present !== true;
  }
  return direction === "pull"
    ? ["only_local", "local_new", "remote_deleted"].includes(status)
    : ["only_remote", "remote_new", "local_deleted"].includes(status);
};

const syncProposedActionLabel = (item, direction) => {
  const status = String(item?.status || "").trim().toLowerCase();
  if (direction === "pull") {
    if (["only_remote", "remote_new", "local_deleted"].includes(status)) return "Add or restore here";
    if (status === "remote_newer") return "Update here";
    if (["only_local", "local_new", "remote_deleted"].includes(status)) return "Delete here";
    if (status === "known_difference") {
      if (item?.remote_present === true && item?.local_present !== true) return "Add here";
      if (item?.local_present === true && item?.remote_present !== true) return "Delete here";
      return "Use remote version here";
    }
  } else {
    if (["only_local", "local_new", "remote_deleted"].includes(status)) return "Add or restore there";
    if (status === "local_newer") return "Update there";
    if (["only_remote", "remote_new", "local_deleted"].includes(status)) return "Delete there";
    if (status === "known_difference") {
      if (item?.local_present === true && item?.remote_present !== true) return "Add there";
      if (item?.remote_present === true && item?.local_present !== true) return "Delete there";
      return "Use local version there";
    }
  }
  if (status === "conflict") return "Resolve both edits";
  if (status === "delete_conflict") return "Resolve edit/delete";
  return "No change";
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
  const statusCounts = syncSectionDiffItems(section).reduce((counts, item) => {
    const status = String(item?.status || "").trim().toLowerCase();
    if (status) counts[status] = Number(counts[status] || 0) + 1;
    return counts;
  }, {});
  const count = (...statuses) =>
    statuses.reduce((total, status) => total + Number(statusCounts[status] || 0), 0);
  const conflicts = count("conflict", "delete_conflict");
  const unresolved = count("known_difference");
  const untracked = count("only_local", "only_remote");
  if (direction === "pull") {
    const additions = count("only_remote", "remote_new", "local_deleted");
    const updates = count("remote_newer");
    const deletions = count("only_local", "local_new", "remote_deleted");
    if (!additions && !updates && !deletions && !conflicts && !unresolved) {
      return identical
        ? `${identical} already match here. Nothing new to pull from ${label}.`
        : `Nothing to pull from ${label}.`;
    }
    const baselineNote = untracked
      ? ` ${untracked} one-sided item${untracked === 1 ? " is" : "s are"} unresolved without a shared checkpoint.`
      : "";
    return `${additions} add or restore here, ${updates} update here, ${deletions} delete here, ${conflicts} conflicts, ${unresolved} known differences, ${identical} already match.${baselineNote}`;
  }
  const additions = count("only_local", "local_new", "remote_deleted");
  const updates = count("local_newer");
  const deletions = count("only_remote", "remote_new", "local_deleted");
  if (!additions && !updates && !deletions && !conflicts && !unresolved) {
    return identical
      ? `${identical} already match there. Nothing to push from this device.`
      : "Nothing to push from this device.";
  }
  const baselineNote = untracked
    ? ` ${untracked} one-sided item${untracked === 1 ? " is" : "s are"} unresolved without a shared checkpoint.`
    : "";
  return `${additions} add or restore there, ${updates} update there, ${deletions} delete there, ${conflicts} conflicts, ${unresolved} known differences, ${identical} already match.${baselineNote}`;
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
      const deleted = Number(section?.deleted || 0);
      const deleteSkipped = Number(section?.delete_skipped || 0);
      const deletePart =
        deleted || deleteSkipped
          ? `, ${deleted} deleted, ${deleteSkipped} delete skipped`
          : "";
      return `${section?.label || "section"}: ${applied} applied, ${skipped} skipped${deletePart}`;
    })
    .join(" | ") || "No changes were applied.";

const inferImportFormatFromFilename = (name) => {
  const value = String(name || "").trim().toLowerCase();
  if (value.endsWith(".zip")) return "zip";
  if (value.endsWith(".json")) return "json";
  if (value.endsWith(".md") || value.endsWith(".markdown")) return "markdown";
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
  if (peer?.remote_device_id && options?.addressDirty) {
    return { key: "pending", label: "new address unverified" };
  }
  if (peer?.remote_device_id && options?.reachable && options?.identityVerified) {
    return { key: "connected", label: "connected" };
  }
  if (peer?.remote_device_id && options?.checked) {
    return options?.reachable
      ? { key: "unreachable", label: "identity not verified" }
      : { key: "unreachable", label: "trusted, check failed" };
  }
  if (peer?.remote_device_id) {
    return { key: "trusted", label: "trusted, not checked" };
  }
  return { key: "saved", label: "saved address - not paired" };
};

const describePeerIdentityStatus = (status) => {
  const state = String(status?.identity_state || "").trim();
  if (state === "verified") return "saved fingerprint matched";
  if (state === "mismatch") return "different Float identity";
  if (state === "missing_remote_identity") return "no remote fingerprint";
  if (state === "unanchored") return "fingerprint not saved yet";
  if (state === "unpaired") return "not compared to a saved pair";
  return "";
};

const shortFingerprint = (value) => {
  const text = String(value || "").trim();
  return text ? text.slice(0, 8) : "";
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

const describeLatestSyncActivity = (action) => {
  if (!action || typeof action !== "object") return "";
  const actionName = String(action.name || "").trim();
  const summary = actionName === "sync_ingest" ? "Incoming sync applied" : "Pull completed";
  const itemCount = Number(action.item_count || 0);
  const itemLabel =
    itemCount > 0 ? `${itemCount} changed item${itemCount === 1 ? "" : "s"}` : "";
  const createdAt = formatDateTime(action.created_at || action.created_at_ts);
  return `Last completed sync: ${summary}${itemLabel ? `, ${itemLabel}` : ""} at ${createdAt}.`;
};

const describeSyncOwnershipTarget = (summary) => {
  const target =
    summary?.outbound_target && typeof summary.outbound_target === "object"
      ? summary.outbound_target
      : {};
  const mode = String(target.mode || "").trim().toLowerCase();
  const remoteUrl = String(target.remote_url || "").trim();
  const peerLabel = String(target.peer_label || "").trim();
  if (mode === "saved_peer" && remoteUrl) {
    return peerLabel || "Saved device";
  }
  if (mode === "manual_url" && remoteUrl) {
    return "Saved address - not paired";
  }
  return "None saved";
};

const describeSyncOwnershipTargetNote = (summary) => {
  const target =
    summary?.outbound_target && typeof summary.outbound_target === "object"
      ? summary.outbound_target
      : {};
  const mode = String(target.mode || "").trim().toLowerCase();
  const remoteUrl = String(target.remote_url || "").trim();
  if (mode === "saved_peer" && remoteUrl) {
    return "Default outbound sync is pinned to a saved paired device.";
  }
  if (mode === "manual_url" && remoteUrl) {
    return "Current outbound sync uses a manual URL. Save it as a pair to pin the remote fingerprint before routine use.";
  }
  return "No default outbound target is configured yet.";
};

const SyncLabelText = ({ text, tooltip }) => (
  <span
    className="knowledge-sync-label-inline"
    title={tooltip || undefined}
    data-tooltip={tooltip || undefined}
    tabIndex={tooltip ? 0 : undefined}
    aria-label={tooltip ? `${text}. ${tooltip}` : undefined}
  >
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
  const [importStatusKind, setImportStatusKind] = useState("");
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
  const [syncPreviewPlanKey, setSyncPreviewPlanKey] = useState("");
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
  const [mobileServeStatus, setMobileServeStatus] = useState(null);
  const [mobileServeBusy, setMobileServeBusy] = useState("");
  const [reviewBusyId, setReviewBusyId] = useState("");
  const [reviewBusyAction, setReviewBusyAction] = useState("");
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
    summary: null,
    classification: "",
    messageCount: 0,
    roleCounts: {},
    preview: "",
    warnings: [],
    suggestedAction: "",
    allowedActions: [],
  });

  const selectedPeer = useMemo(
    () => savedPeers.find((peer) => peer.id === selectedPeerId) || null,
    [savedPeers, selectedPeerId],
  );
  const remoteAddressDirty =
    !!selectedPeer &&
    !!syncRemoteUrl.trim() &&
    syncRemoteUrl.trim() !== String(selectedPeer.remote_url || "").trim();
  const currentSyncPlanKey = useMemo(
    () =>
      JSON.stringify({
        remote_url: syncRemoteUrl.trim(),
        peer_id: selectedPeerId || "",
        scopes: normalizePeerScopes(targetScopes),
        local_workspace_ids: normalizeWorkspaceIdList(selectedWorkspaceIds),
        remote_workspace_ids: normalizeWorkspaceIdList(remoteWorkspaceIds),
        workspace_mode: workspaceMode,
        local_target_workspace_id: localTargetWorkspaceId || activeWorkspaceId || "root",
        remote_target_workspace_id: remoteTargetWorkspaceId || "root",
        link_to_source: !!syncLinkToSourceDevice,
        source_namespace: syncSourceNamespace.trim() || deviceDisplayName.trim(),
      }),
    [
      activeWorkspaceId,
      deviceDisplayName,
      localTargetWorkspaceId,
      remoteTargetWorkspaceId,
      remoteWorkspaceIds,
      selectedPeerId,
      selectedWorkspaceIds,
      syncLinkToSourceDevice,
      syncRemoteUrl,
      syncSourceNamespace,
      targetScopes,
      workspaceMode,
    ],
  );
  const selectedPeerStatusMatches =
    !!selectedPeer &&
    !remoteAddressDirty &&
    peerStatus !== null &&
    syncRemoteUrl.trim() === String(selectedPeer.remote_url || "").trim();
  const selectedPeerConnected =
    selectedPeerStatusMatches &&
    !!selectedPeer.remote_device_id &&
    !!peerStatus?.reachable &&
    !!peerStatus?.identity_verified;
  const selectedPeerState = describePeerStatus(selectedPeer, {
    addressDirty: remoteAddressDirty,
    checked: selectedPeerStatusMatches,
    identityVerified: !!peerStatus?.identity_verified,
    reachable: !!peerStatus?.reachable,
  });
  const selectedPeerIdentityLabel =
    selectedPeer?.label
    || peerStatus?.display_name
    || selectedPeer?.remote_device_name
    || "saved device";
  const selectedPeerConnectionLabel = selectedPeerConnected
    ? `Connected to ${selectedPeerIdentityLabel}`
    : selectedPeer?.remote_device_id
      ? selectedPeerStatusMatches
        ? peerStatus?.reachable
          ? `Trusted ${selectedPeerIdentityLabel}; identity not verified`
          : `Trusted ${selectedPeerIdentityLabel}; last check failed`
        : `Trusted ${selectedPeerIdentityLabel}; not checked`
      : "No active remote";
  const recentSyncActions = useMemo(() => syncHistory.slice(0, 8), [syncHistory]);
  const activeSyncLabel = syncActionBusy || (syncBusy ? "preview" : "");
  const latestSyncActivityLabel = recentSyncActions.length
    ? describeLatestSyncActivity(recentSyncActions[0])
    : "";
  const localDeploymentStatus = overview?.deployment_status || {};
  const localSoftwareStatus = localDeploymentStatus?.software || {};
  const localDataStatus = localDeploymentStatus?.data || {};
  const localDataRevision = localDataStatus?.revision || {};
  const localDataCheckpoint = localDataStatus?.sync_checkpoint || {};
  const remoteDeploymentStatus = peerStatus?.deployment_status || {
    software: selectedPeer?.remote_software || {},
    data: selectedPeer?.remote_data || {},
  };
  const remoteSoftwareComparison = peerStatus?.software_comparison || {};
  const localDeploymentId = String(localDataStatus?.deployment_id || "").trim();
  const localSoftwareLabel =
    String(localSoftwareStatus?.label || "").trim()
    || `${String(localSoftwareStatus?.release_version || "unknown").trim()} // build unassigned`;
  const localDataLabel =
    String(localDataStatus?.display_name || deviceDisplayName || "").trim()
    || (localDeploymentId ? `deployment ${localDeploymentId.slice(0, 8)}` : "deployment identity pending");
  const localDataRevisionCode = String(localDataRevision?.code || "").trim();
  const localDataBuildLabel = localDataRevisionCode
    ? `${localDataLabel} // ${localDataRevisionCode}`
    : localDataLabel;
  const selectedPeerDataCheckpoint = selectedPeer?.data_checkpoint || {};
  const remoteSoftwareLabel = String(remoteDeploymentStatus?.software?.label || "").trim();
  const remoteDataRevisionCode = String(remoteDeploymentStatus?.data?.revision?.code || "").trim();
  const remoteDataLabel =
    String(remoteDeploymentStatus?.data?.display_name || "").trim()
    || (String(remoteDeploymentStatus?.data?.deployment_id || selectedPeer?.remote_deployment_id || "").trim()
      ? `deployment ${String(
          remoteDeploymentStatus?.data?.deployment_id || selectedPeer?.remote_deployment_id,
        ).slice(0, 8)}`
      : "");
  const remoteDataBuildLabel = remoteDataRevisionCode
    ? `${remoteDataLabel || "remote data"} // ${remoteDataRevisionCode}`
    : remoteDataLabel;
  const activeDataCheckpoint = selectedPeer
    ? selectedPeerDataCheckpoint
    : localDataCheckpoint;
  const buildSyncOperationOwner = () =>
    deviceDisplayName ||
    overview?.current_device?.display_name ||
    overview?.current_device?.hostname ||
    "local user";

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
    const requestId = createSyncOperationId(kind);
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
    if (activeRequest.id) {
      axios
        .post(`/api/sync/operations/${encodeURIComponent(activeRequest.id)}/cancel`)
        .catch(() => {});
    }
    setSyncProgress((prev) =>
      prev && prev.id === activeRequest.id
        ? {
            ...prev,
            detail: "Cancel requested.",
            note: "Float is stopping the local request wait; remote work may continue if already accepted.",
          }
        : prev,
    );
    activeRequest.controller.abort("user_cancelled");
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [overviewRes, settingsRes, actionsRes, mobileServeRes] = await Promise.all([
          axios.get("/api/sync/overview"),
          axios.get("/api/user-settings"),
          axios
            .get("/api/actions", { params: { limit: 30, include_reverted: true } })
            .catch(() => ({ data: { actions: [] } })),
          axios.get("/api/sync/mobile-serve/status").catch(() => ({ data: null })),
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
        setPeerStatus(null);
        setPeerStatusBusy(false);
        setSelectedPeerId(match?.id || "");
        setTargetLabel(match?.label || "");
        setTargetScopes(match?.scopes || ["sync"]);
        setWorkspaceMode(match?.workspace_mode || "merge");
        setSelectedWorkspaceIds(
          normalizeWorkspaceIdList(match?.local_workspace_ids).length
            ? normalizeWorkspaceIdList(match.local_workspace_ids)
            : selectedIds,
        );
        setRemoteWorkspaceIds(
          normalizeWorkspaceIdList(match?.remote_workspace_ids).length
            ? normalizeWorkspaceIdList(match.remote_workspace_ids)
            : ["root"],
        );
        setLocalTargetWorkspaceId(match?.local_target_workspace_id || activeId || "root");
        setRemoteTargetWorkspaceId(match?.remote_target_workspace_id || "root");
        setDeviceDisplayName(String(nextOverview?.current_device?.display_name || "").trim());
        setSyncVisibleOnLan(!!nextOverview?.device_access?.visibility?.lan_enabled);
        setSyncAutoAcceptPush(!!nextOverview?.sync_defaults?.auto_accept_push);
        setSyncRemoteUrl(remoteUrl);
        setSyncLinkToSourceDevice(!!nextOverview?.sync_defaults?.link_to_source);
        setSyncSourceNamespace(String(nextOverview?.sync_defaults?.source_namespace || "").trim());
        setSyncHistory(coerceSyncActions(actionsRes?.data?.actions));
        setMobileServeStatus(mobileServeRes?.data || null);
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
    setSyncPreviewPlanKey("");
    setSyncItemSelections({ pull: {}, push: {} });
    setSyncItemReview(null);
    setPeerStatusBusy(false);
    return undefined;
  }, [syncRemoteUrl]);

  useEffect(() => {
    if (!syncPreview || !syncPreviewPlanKey || syncPreviewPlanKey === currentSyncPlanKey) return;
    setSyncPreview(null);
    setSyncPreviewPlanKey("");
    setSyncItemSelections({ pull: {}, push: {} });
    setSyncItemReview(null);
    setMessage("Sync settings changed. Preview changes again before pulling or sending data.");
  }, [currentSyncPlanKey, syncPreview, syncPreviewPlanKey]);

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

  const persistSyncPreferences = async (updates = {}, successMessage = "") => {
    await axios.post("/api/user-settings", updates);
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
      nextSelections.pull[section.key] = syncSectionActionableItems(section, "pull")
        .filter((item) => !syncItemIsDeletion(item, "pull"))
        .map((item) => item?.selection_id || item?.resource_id);
      const pushSection = nextPushSections[section.key] || section;
      nextSelections.push[section.key] = syncSectionActionableItems(pushSection, "push")
        .filter((item) => !syncItemIsDeletion(item, "push"))
        .map((item) => item?.selection_id || item?.resource_id);
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
          const nextPrivacyMode =
            updates?.privacy_mode !== undefined
              ? normalizeWorkspacePrivacyMode(updates.privacy_mode)
              : normalizeWorkspacePrivacyMode(profile.privacy_mode);
          const nextPrivatePatterns =
            updates?.private_patterns !== undefined
              ? normalizeWorkspacePrivatePatterns(updates.private_patterns)
              : normalizeWorkspacePrivatePatterns(profile.private_patterns);
          if (profile.id === "root") {
            return {
              ...profile,
              name:
                typeof updates?.name === "string" && updates.name.trim()
                  ? updates.name.trim()
                  : profile.name,
              privacy_mode: nextPrivacyMode,
              private_patterns: nextPrivatePatterns,
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
            privacy_mode: nextPrivacyMode,
            private_patterns: nextPrivatePatterns,
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
      privacy_mode: "default",
      private_patterns: [],
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
    setSyncPreviewPlanKey("");
    setPeerStatus(null);
    setPeerStatusBusy(false);
    setMessage("");
  };

  const selectSavedPeer = (peer, nextMessage = "") => {
    if (!peer) return;
    setPeerStatus(null);
    setPeerStatusBusy(false);
    setSelectedPeerId(peer.id);
    setTargetLabel(peer.label);
    setTargetScopes(normalizePeerScopes(peer.scopes));
    setSyncRemoteUrl(peer.remote_url);
    setSelectedWorkspaceIds(
      normalizeWorkspaceIdList(peer.local_workspace_ids).length
        ? normalizeWorkspaceIdList(peer.local_workspace_ids)
        : [activeWorkspaceId || "root"],
    );
    setRemoteWorkspaceIds(
      normalizeWorkspaceIdList(peer.remote_workspace_ids).length
        ? normalizeWorkspaceIdList(peer.remote_workspace_ids)
        : ["root"],
    );
    setWorkspaceMode(peer.workspace_mode || "merge");
    setLocalTargetWorkspaceId(peer.local_target_workspace_id || activeWorkspaceId || "root");
    setRemoteTargetWorkspaceId(peer.remote_target_workspace_id || "root");
    setSyncPreview(null);
    setSyncPreviewPlanKey("");
    setMessage(nextMessage);
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
          device_display_name: deviceDisplayName.trim(),
          sync_link_to_source_device: !!syncLinkToSourceDevice,
          sync_source_namespace: resolvedSourceNamespace,
          workspace_profiles: normalizedWorkspaceProfiles,
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
      const response = await axios.post("/api/sync/lan-visibility", {
        enabled: !!nextValue,
        restart: true,
      });
      const result = response?.data || {};
      setMessage(
        result.message
          || (nextValue ? "LAN visibility enabled." : "LAN visibility disabled."),
      );
      setRefreshToken((value) => value + 1);
      if (result.restart_scheduled) {
        window.setTimeout(() => setRefreshToken((value) => value + 1), 2200);
        window.setTimeout(() => setRefreshToken((value) => value + 1), 5200);
      }
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

  const refreshMobileServeStatus = async () => {
    setMobileServeBusy("refresh");
    setMessage("");
    try {
      const res = await axios.get("/api/sync/mobile-serve/status");
      setMobileServeStatus(res?.data || null);
      return res?.data || null;
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to read Mobile Float status."));
      return null;
    } finally {
      setMobileServeBusy("");
    }
  };

  const startMobileServe = async () => {
    const servePort = mobileServeStatus?.serve_port || 64345;
    setMobileServeBusy("start");
    setMessage("");
    try {
      const res = await axios.post("/api/sync/mobile-serve/start", {
        serve_port: servePort,
      });
      const nextStatus = res?.data || null;
      setMobileServeStatus(nextStatus);
      setMessage(nextStatus?.message || `Mobile Float Serve started on port ${servePort}.`);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to start Mobile Float Serve."));
    } finally {
      setMobileServeBusy("");
    }
  };

  const stopMobileServe = async () => {
    const servePort = mobileServeStatus?.serve_port || 64345;
    setMobileServeBusy("stop");
    setMessage("");
    try {
      const res = await axios.post("/api/sync/mobile-serve/stop", {
        serve_port: servePort,
      });
      const nextStatus = res?.data || null;
      setMobileServeStatus(nextStatus);
      setMessage(nextStatus?.message || "Mobile Float Serve stopped.");
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to stop Mobile Float Serve."));
    } finally {
      setMobileServeBusy("");
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
    if (selectedPeer && remoteAddressDirty) {
      setMessage(
        "Verify the changed address before saving it. Float updates a paired address only after the saved fingerprint matches.",
      );
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
    const operationId = createSyncOperationId("check");
    try {
      const res = await axios.post("/api/sync/peer/status", {
        remote_url: remoteUrl,
        paired_device: selectedPeerId ? buildPairedDevicePayload() : null,
        update_saved_peer: !!selectedPeerId && remoteAddressDirty,
        operation_id: operationId,
        operation_owner: buildSyncOperationOwner(),
      });
      const nextStatus = res?.data || null;
      if (syncRemoteUrlRef.current === remoteUrl) {
        setPeerStatus(nextStatus);
        const updatedPeer = coerceSavedPeers(
          nextStatus?.paired_device ? [nextStatus.paired_device] : [],
        )[0];
        if (updatedPeer) {
          mergePairedDeviceRecord(updatedPeer);
          setSyncRemoteUrl(updatedPeer.remote_url);
          setMessage(`Verified ${updatedPeer.label}; saved URL updated.`);
        } else if (nextStatus?.reachable && nextStatus?.identity_verified) {
          setMessage(
            `Connected to ${selectedPeer?.label || nextStatus.display_name || nextStatus.hostname || "remote device"}.`,
          );
        } else if (nextStatus?.reachable) {
          setMessage("The address responded, but its identity was not verified as this saved device.");
        }
      }
      return nextStatus;
    } catch (error) {
      const detail = extractSyncError(
        error,
        "Remote device is not reachable right now.",
        { remote: true },
      );
      const nextStatus = {
        reachable: false,
        error: detail,
      };
      if (syncRemoteUrlRef.current === remoteUrl) {
        setPeerStatus(nextStatus);
        setMessage(
          remoteAddressDirty && selectedPeer
            ? `Could not verify ${remoteUrl}. Saved address remains ${selectedPeer.remote_url}. ${detail}`
            : detail,
        );
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
    if (!selectedPeer?.remote_device_id) {
      setMessage("Pair this saved address using a one-time code before previewing data.");
      return;
    }
    if (selectedPeerId && !normalizePeerScopes(targetScopes).includes("sync")) {
      setMessage("Paired devices used here must include the sync scope.");
      return;
    }
    if (selectedPeerId && remoteAddressDirty) {
      setMessage(
        "Check remote before previewing from a changed address. Float will update the saved URL only if the fingerprint matches.",
      );
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
          operation_id: requestId,
          operation_owner: buildSyncOperationOwner(),
          ...syncOptionsPayload,
        },
        controller ? { signal: controller.signal } : undefined,
      );
      const previewPayload = res?.data || null;
      setSyncPreview(previewPayload);
      setSyncPreviewPlanKey(currentSyncPlanKey);
      setSyncItemSelections(buildSyncItemSelectionState(previewPayload));
      setSyncItemReview(null);
      setPeerStatus((prev) => ({
        ...(prev || {}),
        reachable: true,
        identity_verified: true,
        identity_state: "verified",
        display_name: String(previewPayload?.remote?.display_name || "").trim(),
        hostname: String(previewPayload?.remote?.hostname || "").trim(),
        instance_base: String(previewPayload?.remote?.base_url || remoteUrl).trim(),
        workspaces: previewPayload?.workspaces?.remote || prev?.workspaces || { profiles: [] },
      }));
      if (!targetLabel.trim()) {
        setTargetLabel(String(previewPayload?.remote?.hostname || remoteUrl).trim());
      }
      mergePairedDeviceRecord(previewPayload?.paired_device);
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
        note: extractSyncError(error, "Failed to preview instance sync.", { remote: true }),
      });
      setMessage(
        extractSyncError(error, "Failed to preview instance sync.", { remote: true }),
      );
    } finally {
      setSyncBusy(false);
    }
  };

  const applySync = async (direction) => {
    if (!syncPreview || !syncPreviewPlanKey || syncPreviewPlanKey !== currentSyncPlanKey) {
      setMessage("Preview changes before pulling or sending data.");
      return;
    }
    const sectionSource =
      direction === "push"
        ? pullSections.map((section) => pushSectionMap[section.key] || section)
        : pullSections;
    const sections = [];
    const itemSelections = {};
    sectionSource.forEach((section) => {
      const sectionKey = String(section?.key || "").trim();
      if (!sectionKey) return;
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
    if (selectedPeerId && remoteAddressDirty) {
      setMessage(
        "Check remote before syncing with a changed address. Float will update the saved URL only if the fingerprint matches.",
      );
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
          plan_receipt: syncPreview?.plan_receipt || undefined,
          operation_id: requestId,
          operation_owner: buildSyncOperationOwner(),
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
          { remote: true },
        ),
      });
      setMessage(
        extractSyncError(
          error,
          direction === "push"
            ? "Failed to push data to the remote Float instance."
            : "Failed to pull data from the remote Float instance.",
          { remote: true },
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
        setRemoteWorkspaceIds(
          normalizeWorkspaceIdList(paired.remote_workspace_ids).length
            ? normalizeWorkspaceIdList(paired.remote_workspace_ids)
            : ["root"],
        );
        setPairCodeInput("");
        setRefreshToken((value) => value + 1);
        setMessage(`Paired with ${paired.label}.`);
      } else {
        setMessage("Pairing completed, but the returned device record was incomplete.");
      }
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to pair devices.", { remote: true }));
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
      setMessage(
        extractSyncError(error, "Failed to update remote trust.", { remote: true }),
      );
    } finally {
      setPairSyncBusy(false);
    }
  };

  const revertSyncAction = async (action) => {
    if (!action?.id) return;
    if (
      !window.confirm(
        "Undo this local sync? This restores this device only; it does not change the remote device.",
      )
    ) {
      return;
    }
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
      setSyncPreviewPlanKey("");
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
      setMessage(
        extractSyncError(error, "Failed to revoke remote pair.", { remote: true }),
      );
    } finally {
      setPairSyncBusy(false);
    }
  };

  const approvePendingReview = async (review) => {
    if (!review?.id) return;
    setReviewBusyId(review.id);
    setReviewBusyAction("approve");
    setMessage(
      `Applying sync from ${review.source_label || "remote device"}... This can take a moment while local indexes refresh.`,
    );
    try {
      const res = await axios.post(`/api/sync/reviews/${encodeURIComponent(review.id)}/approve`, {});
      const resultSections = res?.data?.result?.sections || {};
      setMessage(`Approved push from ${review.source_label}. ${summarizeSyncSections(resultSections)}`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to approve incoming push."));
    } finally {
      setReviewBusyId("");
      setReviewBusyAction("");
    }
  };

  const rejectPendingReview = async (review) => {
    if (!review?.id) return;
    setReviewBusyId(review.id);
    setReviewBusyAction("reject");
    setMessage(`Rejecting sync from ${review.source_label || "remote device"}...`);
    try {
      await axios.post(`/api/sync/reviews/${encodeURIComponent(review.id)}/reject`, {});
      setMessage(`Rejected push from ${review.source_label}.`);
      setRefreshToken((value) => value + 1);
    } catch (error) {
      setMessage(extractSyncError(error, "Failed to reject incoming push."));
    } finally {
      setReviewBusyId("");
      setReviewBusyAction("");
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
      summary: null,
      classification: "",
      messageCount: 0,
      roleCounts: {},
      preview: "",
      warnings: [],
      suggestedAction: "",
      allowedActions: [],
    });

  const triggerImportPicker = () => {
    setImportStatus("");
    setImportStatusKind("");
    clearImportReview();
    if (importFileInputRef.current) {
      importFileInputRef.current.value = "";
      importFileInputRef.current.click();
    }
  };

  const previewImportCandidates = async (file) => {
    if (!file) return;
    const format = inferImportFormatFromFilename(file.name);
    const classifiedTextImport = isMarkdownOrTextImport(format);
    const formData = new FormData();
    formData.append("file", file);
    setImportBusy(true);
    setImportStatus("Detecting import candidates...");
    setImportStatusKind("progress");
    try {
      const response = await axios.post("/api/conversations/import/preview", formData);
      if (classifiedTextImport) {
        const classification = normalizeClassifiedImportPreview(response?.data || {}, file);
        setImportReview({
          file,
          detectedFiles: [],
          selectedFiles: {},
          destinationFolder: "",
          summary: response?.data || null,
          ...classification,
        });
        setImportStatus("");
        setImportStatusKind("");
        return;
      }
      const detectedFiles = Array.isArray(response?.data?.detected_files)
        ? response.data.detected_files
        : [];
      if (!detectedFiles.length) {
        setImportStatus("No importable files detected in this archive.");
        setImportStatusKind("error");
        return;
      }
      const selectedFiles = {};
      detectedFiles.forEach((item) => {
        const path = String(item?.path || item?.name || "").trim();
        if (path) selectedFiles[path] = true;
      });
      setImportReview({
        file,
        detectedFiles,
        selectedFiles,
        destinationFolder: "",
        summary: response?.data || null,
      });
      setImportStatus("");
      setImportStatusKind("");
    } catch (error) {
      setImportStatus(extractSyncError(error, "Import preview failed."));
      setImportStatusKind("error");
    } finally {
      setImportBusy(false);
    }
  };

  const uploadConversationImport = async ({
    file,
    selectedFiles = null,
    destinationFolder = "",
    intent = "",
    confirmAmbiguous = false,
  }) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("format", inferImportFormatFromFilename(file.name));
    if (Array.isArray(selectedFiles) && selectedFiles.length) {
      formData.append("selected_files", JSON.stringify(selectedFiles));
    }
    if (destinationFolder) formData.append("destination_folder", destinationFolder);
    if (intent) formData.append("intent", intent);
    if (confirmAmbiguous) formData.append("confirm_ambiguous", "true");
    setImportBusy(true);
    setImportStatus("Importing...");
    setImportStatusKind("progress");
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
      setImportStatusKind("success");
      clearImportReview();
    } catch (error) {
      setImportStatus(extractSyncError(error, "Import failed."));
      setImportStatusKind("error");
    } finally {
      setImportBusy(false);
    }
  };

  const uploadDocumentImport = async ({ file }) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setImportBusy(true);
    setImportStatus("Saving document...");
    setImportStatusKind("progress");
    try {
      await axios.post("/api/knowledge/upload", formData);
      setImportStatus(
        `Saved ${file.name || "file"} to Documents and knowledge search.`,
      );
      setImportStatusKind("success");
      clearImportReview();
    } catch (error) {
      setImportStatus(extractSyncError(error, "Document save failed."));
      setImportStatusKind("error");
    } finally {
      setImportBusy(false);
    }
  };

  const handleImportFileChange = (event) => {
    const file = event?.target?.files?.[0];
    if (!file) return;
    const format = inferImportFormatFromFilename(file.name);
    if (format === "zip" || format === "json" || isMarkdownOrTextImport(format)) {
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
  const importReviewIgnoredJsonCount = Number(
    importReview.summary?.ignored_json_file_count ??
      importReview.summary?.ignored_json_entry_count ??
      0,
  );
  const importReviewIgnoredJsonLabel =
    importReview.summary?.ignored_json_entry_count != null ? "entries" : "files";
  const classifiedImport = Boolean(importReview.classification);
  const classifiedImportDescription = classifiedImport
    ? describeClassifiedImport(importReview)
    : null;
  const classifiedImportActions = new Set(importReview.allowedActions || []);
  const canImportRecognizedConversation =
    classifiedImport &&
    Number(importReview.messageCount || 0) > 0 &&
    classifiedImportActions.has("conversation");
  const canSaveClassifiedDocument =
    classifiedImport && classifiedImportActions.has("document");
  const conversationImportIsPrimary =
    importReview.classification === "conversation" && importReview.suggestedAction !== "document";
  const classifiedRoleSummary = Object.entries(importReview.roleCounts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([role, count]) => `${role}: ${count}`)
    .join(" | ");
  const deviceAccess = overview?.device_access || {};
  const lanVisibility = deviceAccess?.visibility || {};
  const lanListener = deviceAccess?.listener || {};
  const advertisedUrls = deviceAccess?.advertised_urls || {};
  const lanUrl = String(advertisedUrls?.lan || "").trim();
  const lanCandidateUrl = String(advertisedUrls?.lan_candidate || "").trim();
  const lanListening = lanVisibility?.lan_listening === true;
  const lanReady = !!syncVisibleOnLan && lanListening;
  const lanStatusLabel = lanReady
    ? "listening"
    : syncVisibleOnLan
      ? visibilityBusy === "lan"
        ? "restarting"
        : "restart needed"
      : lanListening
        ? "stopping"
        : "off";
  const lanStatusTone = lanReady ? "on" : syncVisibleOnLan || lanListening ? "pending" : "off";
  const internetUrl = String(advertisedUrls?.internet || "").trim();
  const localWorkspaceProfiles = coerceWorkspaceProfiles(workspaceProfiles);
  const remoteWorkspaceState =
    peerStatus?.workspaces || syncPreview?.workspaces?.remote || { profiles: [] };
  const remoteWorkspaceProfiles = coerceWorkspaceProfiles(remoteWorkspaceState?.profiles);
  const inboundDevices = Array.isArray(overview?.inbound_devices) ? overview.inbound_devices : [];
  const legacyInboundDevices = Array.isArray(overview?.legacy_inbound_devices)
    ? overview.legacy_inbound_devices
    : [];
  const trustedInboundDevices = inboundDevices.filter((device) => !device?.legacy_record);
  const visibleLegacyInboundDevices = [
    ...legacyInboundDevices,
    ...inboundDevices.filter((device) => device?.legacy_record),
  ].filter(
    (device, index, list) =>
      list.findIndex((entry) => String(entry?.id || "").trim() === String(device?.id || "").trim())
      === index,
  );
  const pendingReviews = Array.isArray(overview?.sync_reviews?.pending) ? overview.sync_reviews.pending : [];
  const recentReviews = Array.isArray(overview?.sync_reviews?.recent) ? overview.sync_reviews.recent : [];
  const deviceCounts = overview?.device_counts || {};
  const syncOwnershipSummary =
    overview?.egress_summary && typeof overview.egress_summary === "object"
      ? overview.egress_summary
      : null;
  const syncOperations =
    overview?.sync_operations && typeof overview.sync_operations === "object"
      ? overview.sync_operations
      : {};
  const syncSuggestions = Array.isArray(overview?.sync_suggestions)
    ? [...overview.sync_suggestions]
        .filter((entry) => entry && typeof entry === "object")
        .sort(
          (left, right) =>
            Number(left.priority || 100) - Number(right.priority || 100)
            || String(left.id || "").localeCompare(String(right.id || "")),
        )
    : [];
  const activeSyncOperation =
    syncOperations.active_operation && typeof syncOperations.active_operation === "object"
      ? syncOperations.active_operation
      : null;
  const lastSyncOperation =
    syncOperations.last_attempt && typeof syncOperations.last_attempt === "object"
      ? syncOperations.last_attempt
      : null;
  const syncOwnershipTargetLabel = describeSyncOwnershipTarget(syncOwnershipSummary);
  const syncOwnershipTargetNote = describeSyncOwnershipTargetNote(syncOwnershipSummary);
  const syncOwnershipLanEnabled =
    syncOwnershipSummary?.inbound_visibility?.lan_enabled === true || !!syncVisibleOnLan;
  const syncOwnershipPushMode =
    String(syncOwnershipSummary?.push_review_mode || "").trim().toLowerCase() === "auto_accept"
      ? "Auto-accept"
      : "Review required";
  const syncOwnershipAutoSync =
    syncOwnershipSummary?.auto_sync && typeof syncOwnershipSummary.auto_sync === "object"
      ? syncOwnershipSummary.auto_sync
      : {};
  const syncOwnershipAutoSyncLabel = syncOwnershipAutoSync.enabled ? "Enabled" : "Off";
  const syncOwnershipBackgroundOwner =
    syncOwnershipSummary?.background_owner && typeof syncOwnershipSummary.background_owner === "object"
      ? syncOwnershipSummary.background_owner
      : {};
  const syncOwnershipSavedPeerCount =
    Number.isFinite(syncOwnershipSummary?.saved_peer_count)
      ? Number(syncOwnershipSummary.saved_peer_count)
      : savedPeers.length;
  const syncOwnershipUnfinishedNote =
    String(syncOwnershipSummary?.unfinished_notice || "").trim()
    || "Backend retry ownership is not fully tracked yet.";
  const activeSyncOperationDescription = activeSyncOperation
    ? describeSyncOperation(activeSyncOperation)
    : "none";
  const lastSyncOperationDescription = lastSyncOperation
    ? describeSyncOperation(lastSyncOperation)
    : "none recorded";
  const syncOwnershipInspectorRows = buildSyncOwnershipInspectorRows({
    syncOwnershipSummary: {
      ...syncOwnershipSummary,
      source_namespace:
        deviceDisplayName ||
        overview?.current_device?.display_name ||
        overview?.current_device?.hostname ||
        syncOwnershipSummary?.source_namespace,
      default_target_label: syncOwnershipTargetLabel,
    },
    activeOperation: activeSyncOperation,
    lastOperation: lastSyncOperation,
    activeDescription: activeSyncOperationDescription,
    lastDescription: lastSyncOperationDescription,
  });
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
  const selectedPullItemCount = Object.values(syncItemSelections?.pull || {}).reduce(
    (total, value) => total + normalizeSyncSelectionIds(value).length,
    0,
  );
  const selectedPushItemCount = Object.values(syncItemSelections?.push || {}).reduce(
    (total, value) => total + normalizeSyncSelectionIds(value).length,
    0,
  );
  const unselectedDeletionCount = pullSections.reduce((total, section) => {
    const pullDeletes = syncSectionActionableItems(section, "pull").filter((item) =>
      syncItemIsDeletion(item, "pull"),
    ).length;
    const pushSection = pushSectionMap[section.key] || section;
    const pushDeletes = syncSectionActionableItems(pushSection, "push").filter((item) =>
      syncItemIsDeletion(item, "push"),
    ).length;
    return total + pullDeletes + pushDeletes;
  }, 0);
  const selectedPeerLabel =
    String(
      selectedPeer?.label
      || peerStatus?.display_name
      || selectedPeer?.remote_device_name
      || "",
    ).trim() || "remote device";
  const peerCheckActionLabel = peerStatusBusy
    ? "Checking..."
    : remoteAddressDirty
      ? "Check and save new address"
      : "Check remote";
  const previewIsCurrent =
    !!syncPreview && !!syncPreviewPlanKey && syncPreviewPlanKey === currentSyncPlanKey;
  const canPreviewSync =
    !!selectedPeer?.remote_device_id && !remoteAddressDirty && !syncBusy && !syncActionBusy;
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
  const syncItemReviewDeletionIds = syncItemReviewItems
    .filter((item) => syncItemIsDeletion(item, syncItemReviewDirection))
    .map((item) => String(item?.selection_id || item?.resource_id || "").trim())
    .filter(Boolean);
  const syncItemReviewSelectedDeletionCount = syncItemReviewDeletionIds.filter((itemId) =>
    syncItemReviewSelectedIds.includes(itemId),
  ).length;
  const importModeInvalid =
    workspaceMode === "import" &&
    (normalizeWorkspaceIdList(selectedWorkspaceIds).length !== 1
      || normalizeWorkspaceIdList(remoteWorkspaceIds).length !== 1);
  const mobileServeTone = mobileServeBusy
    ? "pending"
    : mobileServeStatus?.running
      ? "paired"
      : mobileServeStatus?.ok
        ? "saved"
        : mobileServeStatus
          ? "legacy"
          : "saved";
  const mobileServeLabel = mobileServeBusy
    ? mobileServeBusy === "refresh"
      ? "checking"
      : "working"
    : mobileServeStatus?.running
      ? "running"
      : mobileServeStatus?.ok
        ? "ready"
        : "not ready";
  const mobileServePort = mobileServeStatus?.serve_port || 64345;
  const mobileServeUrl = String(mobileServeStatus?.url || "").trim();

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
        <button
          type="button"
          className="icon-btn knowledge-sync-action--quiet"
          onClick={() => setRefreshToken((value) => value + 1)}
        >
          Refresh
        </button>
      </div>

      {message ? <p className="status-note">{message}</p> : null}
      {!syncProgress && latestSyncActivityLabel ? (
        <p
          className="status-note"
          title="Most recent completed sync action recorded locally."
        >
          {latestSyncActivityLabel}
        </p>
      ) : null}
      <section className="knowledge-sync-card knowledge-sync-card--active-flow">
        <div className="knowledge-sync-section-head">
          <div className="knowledge-sync-section-stack">
            <h4>Sync with a device</h4>
            <span className="status-note">
              Select a paired device, preview the changes, then choose what to pull here or send there.
            </span>
          </div>
          <span
            className={`knowledge-sync-target-status is-${
              selectedPeer ? selectedPeerState.key : "saved"
            }`}
          >
            {selectedPeer ? selectedPeerState.label : "pair required"}
          </span>
        </div>
        <label className="field-label" htmlFor="sync-active-peer">
          <span>Device</span>
        </label>
        <select
          id="sync-active-peer"
          value={selectedPeerId}
          onChange={(event) => {
            const peer = savedPeers.find((entry) => entry.id === event.target.value);
            if (peer) selectSavedPeer(peer);
            else resetPairEditor();
          }}
        >
          <option value="">Choose a saved device</option>
          {savedPeers.map((peer) => (
            <option key={`active-${peer.id}`} value={peer.id}>
              {peer.label}{peer.remote_device_id ? "" : " - not paired"}
            </option>
          ))}
        </select>
        {selectedPeer ? (
          <div className="knowledge-sync-target-meta">
            <span>{selectedPeerLabel}</span>
            <span>{selectedPeer.remote_device_id ? "Trust record present" : "Saved address only"}</span>
            {shortFingerprint(selectedPeer.remote_public_key) ? (
              <span>fingerprint {shortFingerprint(selectedPeer.remote_public_key)}</span>
            ) : (
              <span>fingerprint not saved</span>
            )}
          </div>
        ) : (
          <p className="status-note">Pair or save a device in Connection setup below.</p>
        )}
        <div className="knowledge-sync-actions">
          {remoteAddressDirty ? (
            <button
              type="button"
              className="icon-btn knowledge-sync-action--quiet"
              onClick={checkPeerStatus}
              disabled={peerStatusBusy}
            >
              {peerCheckActionLabel}
            </button>
          ) : null}
          <button
            type="button"
            className="icon-btn knowledge-sync-action--primary"
            onClick={previewSync}
            disabled={!canPreviewSync}
          >
            {syncBusy ? "Previewing..." : "Preview changes"}
          </button>
          <button
            type="button"
            className="icon-btn knowledge-sync-action--secondary"
            onClick={() => applySync("pull")}
            disabled={!previewIsCurrent || !selectedPullItemCount || !!syncActionBusy}
            title={!previewIsCurrent ? "Preview required" : undefined}
          >
            {syncActionBusy === "pull" ? "Pulling..." : `Pull ${selectedPullItemCount || "selected"} here`}
          </button>
          <button
            type="button"
            className="icon-btn knowledge-sync-action--secondary"
            onClick={() => applySync("push")}
            disabled={!previewIsCurrent || !selectedPushItemCount || !!syncActionBusy}
            title={!previewIsCurrent ? "Preview required" : undefined}
          >
            {syncActionBusy === "push"
              ? "Sending..."
              : `Send ${selectedPushItemCount || "selected"} to ${selectedPeerLabel}`}
          </button>
        </div>
        {!previewIsCurrent ? (
          <p className="status-note">Preview required before pull or send actions become available.</p>
        ) : unselectedDeletionCount ? (
          <p className="status-note">
            {unselectedDeletionCount} proposed deletion{unselectedDeletionCount === 1 ? " is" : "s are"} off by default. Review items to include any deletion.
          </p>
        ) : null}
        {selectedPeer ? (
          <details className="knowledge-sync-connection-details">
            <summary>Connection details</summary>
            <div className="knowledge-sync-target-meta">
              <span>Last known address: {selectedPeer.remote_url}</span>
              {peerStatus?.advertised_lan_url ? (
                <span>Advertised address: {peerStatus.advertised_lan_url}</span>
              ) : null}
            </div>
          </details>
        ) : null}
      </section>
      <section className="knowledge-sync-card knowledge-sync-card--compact-status">
        <div className="knowledge-sync-section-head">
          <h4>
            <SyncLabelText
              text="Sync ownership"
              tooltip="Summarizes the default outbound target, inbound exposure, and push review posture for this device."
            />
          </h4>
          <StateInspector
            title="Why this sync state is shown"
            summary="This card is built from local sync overview, egress defaults, and active/last sync operation telemetry."
            rows={syncOwnershipInspectorRows}
            ariaLabel="Explain sync ownership state"
          />
          <span className={`knowledge-sync-target-status is-${syncOwnershipLanEnabled ? "paired" : "saved"}`}>
            {syncOwnershipSummary?.private_network_only ? "private only" : "review"}
          </span>
        </div>
        <p className="status-note">
          Outbound target, inbound exposure, and review posture for this device.
        </p>
        <dl className="knowledge-sync-meta">
          <div>
            <dt>Default outbound</dt>
            <dd>{syncOwnershipTargetLabel}</dd>
          </div>
          <div>
            <dt>Inbound exposure</dt>
            <dd>{syncOwnershipLanEnabled ? "LAN enabled" : "Local only"}</dd>
          </div>
          <div>
            <dt>Push handling</dt>
            <dd>{syncOwnershipPushMode}</dd>
          </div>
          <div>
            <dt>Auto sync</dt>
            <dd>{syncOwnershipAutoSyncLabel}</dd>
          </div>
          <div>
            <dt>Saved peers</dt>
            <dd>{syncOwnershipSavedPeerCount}</dd>
          </div>
          <div>
            <dt>Background owner</dt>
            <dd>
              {syncOwnershipBackgroundOwner.mode === "active"
                ? syncOwnershipBackgroundOwner.active_owner || "This device"
                : "Idle"}
            </dd>
          </div>
          <div>
            <dt>Active request</dt>
            <dd>{activeSyncOperation ? activeSyncOperationDescription : "None"}</dd>
          </div>
          <div>
            <dt>Last attempt</dt>
            <dd>{lastSyncOperation ? lastSyncOperationDescription : "None recorded"}</dd>
          </div>
        </dl>
        <div className="knowledge-sync-preview-chip-row">
          <span className="knowledge-sync-preview-chip">
            <strong>egress</strong>
            <span>Private reachable addresses only</span>
          </span>
          <span className="knowledge-sync-preview-chip">
            <strong>outbound</strong>
            <span>{syncOwnershipSummary?.outbound_target?.mode === "saved_peer" ? "Pinned pair" : syncOwnershipSummary?.outbound_target?.mode === "manual_url" ? "Manual URL" : "Unset"}</span>
          </span>
          <span className="knowledge-sync-preview-chip">
            <strong>push</strong>
            <span>{syncOwnershipPushMode}</span>
          </span>
          <span className="knowledge-sync-preview-chip">
            <strong>auto sync</strong>
            <span>{syncOwnershipAutoSyncLabel}</span>
          </span>
          <span className="knowledge-sync-preview-chip">
            <strong>operation</strong>
            <span>{activeSyncOperation ? syncOperationStatusLabel(activeSyncOperation.status) : "Idle"}</span>
          </span>
        </div>
        <div className="knowledge-sync-section-stack">
          <span className="status-note">{syncOwnershipTargetNote}</span>
          <span
            className="status-note"
            title="This gap is tracked intentionally as part of the spring-cleaning pass."
          >
            Unfinished: {syncOwnershipUnfinishedNote}
          </span>
        </div>
      </section>
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
                <button
                  type="button"
                  className="icon-btn knowledge-sync-action--danger"
                  onClick={cancelActiveSync}
                  title="Record cancel intent and stop waiting on the current sync request."
                  aria-label={`Stop ${activeSyncLabel || "sync"} request`}
                >
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

      {syncSuggestions.length ? (
        <section className="knowledge-sync-card knowledge-sync-card--sync-inbox">
          <div className="knowledge-sync-section-head">
            <h4>
              <SyncLabelText
                text="Sync inbox"
                tooltip="Local suggestions for safe manual sync setup. Auto-sync stays off in this pass."
              />
            </h4>
            <span className="knowledge-sync-target-status is-pending">
              {syncSuggestions.length} suggestion{syncSuggestions.length === 1 ? "" : "s"}
            </span>
          </div>
          <p className="status-note">
            Suggestions only. Nothing checks, pulls, pushes, or applies until you start that action.
          </p>
          <div className="knowledge-sync-suggestion-list">
            {syncSuggestions.map((suggestion) => {
              const explanation =
                suggestion?.state_explanation && typeof suggestion.state_explanation === "object"
                  ? suggestion.state_explanation
                  : null;
              const requirements = Array.isArray(suggestion?.requirements)
                ? suggestion.requirements.filter((entry) => entry && typeof entry === "object")
                : [];
              const suggestedPeer = savedPeers.find((peer) => peer.id === suggestion.peer_id);
              return (
                <article
                  key={suggestion.id || suggestion.title}
                  className={`knowledge-sync-suggestion is-${syncSuggestionTone(suggestion.severity)}`}
                >
                  <div className="knowledge-sync-section-head">
                    <div className="knowledge-sync-section-stack">
                      <strong>{suggestion.title || "Sync suggestion"}</strong>
                      {suggestion.summary ? (
                        <span className="status-note">{suggestion.summary}</span>
                      ) : null}
                    </div>
                    <div className="knowledge-sync-head-actions">
                      {explanation ? (
                        <StateInspector
                          title={explanation.title || "Why this sync suggestion is shown"}
                          summary={explanation.summary || ""}
                          rows={explanation.rows || []}
                          ariaLabel={`Explain sync suggestion ${suggestion.title || ""}`.trim()}
                        />
                      ) : null}
                      <span
                        className={`knowledge-sync-target-status is-${syncSuggestionTone(
                          suggestion.severity,
                        )}`}
                      >
                        {syncSuggestionSeverityLabel(suggestion.severity)}
                      </span>
                    </div>
                  </div>
                  <div className="knowledge-sync-preview-chip-row">
                    <span className="knowledge-sync-preview-chip">
                      <strong>next</strong>
                      <span>{suggestion.action_label || "Review"}</span>
                    </span>
                    <span className="knowledge-sync-preview-chip">
                      <strong>auto sync</strong>
                      <span>{suggestion.auto_sync_enabled ? "On" : "Off"}</span>
                    </span>
                    <span className="knowledge-sync-preview-chip">
                      <strong>review</strong>
                      <span>{suggestion.manual_review_required === false ? "Optional" : "Required"}</span>
                    </span>
                    {suggestion.peer_label || suggestion.remote_url ? (
                      <span className="knowledge-sync-preview-chip">
                        <strong>peer</strong>
                        <span>{suggestion.peer_label || suggestion.remote_url}</span>
                      </span>
                    ) : null}
                  </div>
                  {requirements.length ? (
                    <div className="knowledge-sync-suggestion-requirements">
                      {requirements.map((requirement) => (
                        <span
                          key={`${suggestion.id}-${requirement.label}`}
                          className={`knowledge-sync-requirement is-${String(
                            requirement.status || "",
                          )
                            .trim()
                            .toLowerCase()}`}
                          title={requirement.detail || requirement.label}
                        >
                          <strong>{requirement.label}</strong>
                          <span>{requirement.detail || requirement.status}</span>
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="knowledge-sync-section-head knowledge-sync-suggestion-footer">
                    <span className="status-note">{suggestion.next_step || suggestion.summary}</span>
                    {suggestedPeer ? (
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() =>
                          selectSavedPeer(
                            suggestedPeer,
                            `Selected ${suggestedPeer.label}. Run Check remote when you are ready.`,
                          )
                        }
                      >
                        Select pair
                      </button>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      <div className="knowledge-sync-grid knowledge-sync-dashboard-grid">
        <section className="knowledge-sync-card knowledge-sync-card--deployment">
          <div className="knowledge-sync-section-head">
            <h4>
              <SyncLabelText
                text="Deployment status"
                tooltip="Software and data are separate, equal status dimensions. Software uses version plus build; data uses deployment identity plus a deterministic content revision and peer checkpoints."
              />
            </h4>
          </div>
          <div className="knowledge-sync-preview-grid">
            <span className="knowledge-sync-preview-chip" data-status-dimension="software">
              <strong>software</strong>
              <span>{localSoftwareLabel}</span>
            </span>
            <span className="knowledge-sync-preview-chip" data-status-dimension="data">
              <strong>data</strong>
              <span>{localDataBuildLabel}</span>
            </span>
          </div>
          <div className="knowledge-sync-card-subtle">
            <span>
              Software uses the stable release version plus an occasional build checkpoint.
              Exact snapshot digests remain available underneath.
            </span>
            <span>
              Data owns {Number(localDataStatus?.workspace_count || localWorkspaceProfiles.length)} workspace
              {Number(localDataStatus?.workspace_count || localWorkspaceProfiles.length) === 1 ? "" : "s"}
              {localDeploymentId ? ` under deployment ${localDeploymentId.slice(0, 8)}` : ""}.
            </span>
            {localDataRevisionCode ? (
              <span>
                Data revision {localDataRevisionCode}; last local revision detected{" "}
                {formatDateTime(localDataStatus?.last_updated_at || localDataRevision?.observed_at_iso)}.
              </span>
            ) : null}
            {activeDataCheckpoint?.last_synced_at || activeDataCheckpoint?.last_synced_at_iso ? (
              <span>
                Last synced {formatDateTime(
                  activeDataCheckpoint?.last_synced_at || activeDataCheckpoint?.last_synced_at_iso,
                )}
                {activeDataCheckpoint?.peer_label ? ` with ${activeDataCheckpoint.peer_label}` : ""}.{" "}
                {activeDataCheckpoint?.summary || ""}
              </span>
            ) : activeDataCheckpoint?.last_verified_at || activeDataCheckpoint?.last_verified_at_iso ? (
              <span>
                Common data state last verified{" "}
                {formatDateTime(
                  activeDataCheckpoint?.last_verified_at || activeDataCheckpoint?.last_verified_at_iso,
                )}. {activeDataCheckpoint?.summary || ""}
              </span>
            ) : (
              <span>No successful data sync checkpoint has been recorded yet.</span>
            )}
            {peerStatus?.reachable ? (
              <span>
                Remote software: {remoteSoftwareComparison?.summary || "comparison unavailable"}. Remote data:{" "}
                {remoteDataBuildLabel
                  || peerStatus?.display_name
                  || selectedPeer?.label
                  || "reachable deployment"}.
              </span>
            ) : selectedPeer && (remoteSoftwareLabel || remoteDataLabel) ? (
              <span>
                Last known peer status: software {remoteSoftwareLabel || "unknown"}; data {remoteDataBuildLabel || "identity unavailable"}.
              </span>
            ) : null}
          </div>
        </section>

        <section className="knowledge-sync-card knowledge-sync-card--current-device">
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
            <strong title={WORKSPACE_MAPPING_HELP}>
              Workspaces
            </strong>
            <div className="knowledge-sync-workspace-list">
              {localWorkspaceProfiles.map((profile) => {
                const imported = profile.imported === true || profile.kind === "synced";
                const privacyMode = workspacePrivacyLabel(profile);
                const privacyTooltip = getWorkspacePrivacyTooltip(privacyMode);
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
                        <span title={privacyTooltip}>sync</span>
                      </label>
                      <div className="knowledge-sync-workspace-badges">
                        <span
                          className={`knowledge-sync-target-status is-${
                            imported ? "connected" : profile.is_root ? "paired" : "saved"
                          }`}
                        >
                          {imported ? "imported" : profile.is_root ? "root" : "local"}
                        </span>
                        <span
                          className={`knowledge-sync-target-status is-${privacyMode}`}
                          title={privacyTooltip}
                        >
                          {privacyMode}
                        </span>
                      </div>
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
                      <label className="field-label">
                        <span title={privacyTooltip}>Workspace privacy</span>
                        <select
                          aria-label={`Workspace privacy for ${profile.name}`}
                          value={privacyMode}
                          onChange={(event) =>
                            updateWorkspaceProfile(profile.id, {
                              privacy_mode: event.target.value,
                            })
                          }
                          title={privacyTooltip}
                        >
                          {WORKSPACE_PRIVACY_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <label className="field-label">
                      <span title={WORKSPACE_PRIVATE_PATTERNS_HELP}>Private match rules</span>
                      <textarea
                        aria-label={`Private match rules for ${profile.name}`}
                        value={workspacePrivatePatternsText(profile.private_patterns)}
                        onChange={(event) =>
                          updateWorkspaceProfile(profile.id, {
                            private_patterns: event.target.value,
                          })
                        }
                        rows={3}
                        placeholder={"notes/private/*\n*.pem"}
                        title={WORKSPACE_PRIVATE_PATTERNS_HELP}
                      />
                    </label>
                    {imported ? (
                      <div className="status-note">
                        Source: {profile.source_device_name || "remote"} /{" "}
                        {profile.source_workspace_name || profile.name}
                        {profile.upstream_deployment_id ? (
                          <>
                            {" "}
                            · sync back to{" "}
                            <span title={profile.upstream_deployment_id}>
                              {profile.upstream_deployment_id.slice(0, 12)}
                            </span>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                    {profile.lineage_id ? (
                      <div className="status-note" title={profile.lineage_id}>
                        Lineage {profile.lineage_id.slice(0, 12)}
                        {profile.origin_deployment_id ? (
                          <>
                            {" "}
                            · origin{" "}
                            <span title={profile.origin_deployment_id}>
                              {profile.origin_deployment_id.slice(0, 12)}
                            </span>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                    {workspaceSyncBlocked(privacyMode) ? (
                      <div className="status-note" title={privacyTooltip}>
                        This workspace stays out of sync and default recall while privacy is{" "}
                        {privacyMode}.
                      </div>
                    ) : null}
                    {normalizeWorkspacePrivatePatterns(profile.private_patterns).length ? (
                      <div className="status-note" title={WORKSPACE_PRIVATE_PATTERNS_HELP}>
                        Private rules active:{" "}
                        {normalizeWorkspacePrivatePatterns(profile.private_patterns).length}
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
            <strong title={LAN_VISIBILITY_HELP}>
              Device visibility
            </strong>
            <span className="status-note">
              You can use any private reachable address here, not just the local home-network one.
            </span>
          </div>
          <div className="knowledge-sync-visibility-grid">
            <label className="knowledge-sync-visibility-card" title={LAN_VISIBILITY_HELP}>
              <div className="knowledge-sync-visibility-header">
                <div className="knowledge-sync-section-stack">
                  <strong>Visible on LAN</strong>
                  <span className={`knowledge-sync-visibility-badge is-${lanStatusTone}`}>
                    {lanStatusLabel}
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
                <span className="knowledge-sync-url-label">
                  {lanReady ? "Listening at" : "Candidate LAN URL"}
                </span>
                <code>{lanUrl || lanCandidateUrl || "Unable to detect a LAN URL from this session."}</code>
              </div>
              <span className="status-note">
                {lanReady
                  ? "The backend is bound to the private network and paired devices may connect."
                  : syncVisibleOnLan
                    ? lanListener?.binding_locked
                      ? "The preference is on, but an explicit launcher bind-host override controls this session. Restart Float without a host or LAN override to let this switch manage the listener."
                      : lanListener?.reload_enabled
                        ? "The preference is on, but backend auto-reload is active. Restart Float without --dev to let this switch manage the listener."
                      : lanListener?.restart_supported
                        ? "The preference is on, but the listener has not restarted yet. Float should reconnect on the same port."
                        : "The preference is on, but this session is not launcher-managed. Restart Float to begin listening on LAN."
                    : lanListening
                      ? "Turning this off restarts the backend in device-only mode."
                      : "Turn this on to restart the backend with private-network listening."}
              </span>
            </label>
            <div className="knowledge-sync-visibility-card" title={MOBILE_FLOAT_HELP}>
              <div className="knowledge-sync-visibility-header">
                <div className="knowledge-sync-section-stack">
                  <strong>Mobile Float</strong>
                  <span className={`knowledge-sync-visibility-badge is-${mobileServeStatus?.running ? "on" : mobileServeStatus?.ok ? "off" : "disabled"}`}>
                    {mobileServeLabel}
                  </span>
                </div>
                <span className={`knowledge-sync-target-status is-${mobileServeTone}`}>
                  :{mobileServePort}
                </span>
              </div>
              <div className="knowledge-sync-url-stack">
                <span className="knowledge-sync-url-label">Tailscale URL</span>
                <code>{mobileServeUrl || "Not served yet."}</code>
              </div>
              <div className="knowledge-sync-target-meta">
                <span>{mobileServeStatus?.target || "local frontend unknown"}</span>
                {mobileServeStatus?.tailnet_host ? <span>{mobileServeStatus.tailnet_host}</span> : null}
              </div>
              <span className="status-note">
                {mobileServeStatus?.warning || (mobileServeStatus?.running ? "Tailnet devices can open this URL." : "Ready to serve over Tailscale.")}
              </span>
              <div className="knowledge-sync-head-actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={startMobileServe}
                  disabled={mobileServeBusy === "start" || !mobileServeStatus?.ok}
                  title={MOBILE_FLOAT_HELP}
                >
                  {mobileServeBusy === "start" ? "Starting..." : "Start serve"}
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={stopMobileServe}
                  disabled={mobileServeBusy === "stop" || !mobileServeStatus?.installed}
                >
                  {mobileServeBusy === "stop" ? "Stopping..." : "Stop"}
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={refreshMobileServeStatus}
                  disabled={mobileServeBusy === "refresh"}
                >
                  {mobileServeBusy === "refresh" ? "Checking..." : "Status"}
                </button>
              </div>
            </div>
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
            <div className="knowledge-sync-visibility-card is-disabled" title={ONLINE_VISIBILITY_HELP}>
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
              <span className="status-note">Public internet sync is not supported yet.</span>
            </div>
          </div>
          <button type="button" className="icon-btn" onClick={saveDeviceSettings} disabled={savingPrefs}>
            {savingPrefs ? "Saving..." : "Save device settings"}
          </button>
          <div className="knowledge-sync-card-subtle">
            <strong>
              <SyncLabelText text="Pairing code" tooltip={PAIRING_CODE_HELP} />
            </strong>
            <span>
              Generate a one-time code on this device, then enter it from another trusted device.
            </span>
            <button
              type="button"
              className="icon-btn"
              onClick={createPairingOffer}
              disabled={pairBusy || !lanReady}
              title={PAIRING_CODE_HELP}
            >
              {pairBusy ? "Creating..." : "Generate pairing code"}
            </button>
            {!lanReady ? (
              <span className="status-note">Start the LAN listener before inviting another device.</span>
            ) : null}
            {localPairOffer ? (
              <div className="knowledge-sync-section-stack">
                <strong className="knowledge-sync-offer-code">{localPairOffer.code}</strong>
                <span className="status-note">Expires {formatDateTime(localPairOffer.expires_at)}</span>
              </div>
            ) : null}
          </div>
        </section>

        <section className="knowledge-sync-card knowledge-sync-card--connection">
          <div className="knowledge-sync-section-head">
            <h4>{selectedPeerId ? "Edit connection" : "Connection setup"}</h4>
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
              tooltip={REMOTE_URL_HELP}
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
              Address change. Check the new address; if the saved fingerprint matches, Float updates this connection automatically. A failed check leaves the saved address unchanged.
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
                    title={
                      peerStatusBusy
                        ? "Checking the remote URL now."
                        : peerStatus?.reachable
                          ? "The remote URL responded and the device is reachable."
                          : peerStatus
                            ? "The remote URL did not respond as expected."
                            : "No remote check has been run yet."
                    }
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
                    title={CHECK_REMOTE_HELP}
                  >
                    {peerCheckActionLabel}
                  </button>
                </div>
              </div>
              {peerStatus?.reachable ? (
                <div className="knowledge-sync-target-meta">
                  <span>{peerStatus.display_name || peerStatus.hostname || "Remote device"}</span>
                  {typeof peerStatus.visible_on_lan === "boolean" ? (
                    <span
                      title={
                        peerStatus.visible_on_lan
                          ? "The remote device advertises a private-network address."
                          : "The remote device is not advertising a private-network address."
                      }
                    >
                      {peerStatus.visible_on_lan ? "LAN visible" : "LAN hidden"}
                    </span>
                  ) : null}
                  {selectedPeer?.remote_device_id && !remoteAddressDirty ? (
                    <span>{selectedPeerConnected ? "Connected to this saved pair" : "Saved pair is reachable"}</span>
                  ) : null}
                  {describePeerIdentityStatus(peerStatus) ? (
                    <span title={peerStatus.identity_warning || "Stable device identity check."}>
                      {describePeerIdentityStatus(peerStatus)}
                    </span>
                  ) : null}
                  {shortFingerprint(peerStatus?.identity?.public_key) ? (
                    <span>fingerprint {shortFingerprint(peerStatus.identity.public_key)}</span>
                  ) : null}
                  {peerStatus.advertised_lan_url ? <span>{peerStatus.advertised_lan_url}</span> : null}
                  {peerStatus.identity_warning ? (
                    <span title={peerStatus.identity_warning}>{peerStatus.identity_warning}</span>
                  ) : null}
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
            <strong title={WORKSPACE_MAPPING_HELP}>
              Workspace mapping
            </strong>
            <div className="knowledge-sync-inline-mode-row">
              <label className="knowledge-sync-inline-toggle" title="Merge writes into the selected destination workspace.">
                <input
                  type="radio"
                  checked={workspaceMode === "merge"}
                  onChange={() => setWorkspaceMode("merge")}
                />
                <span>merge</span>
              </label>
              <label className="knowledge-sync-inline-toggle" title="Import nested creates a new imported workspace under the selected destination.">
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
                  {localWorkspaceProfiles.map((profile) => {
                    const privacyMode = workspacePrivacyLabel(profile);
                    return (
                      <label
                        key={`local-source-${profile.id}`}
                        className="knowledge-sync-scope-toggle"
                        title={getWorkspacePrivacyTooltip(privacyMode)}
                      >
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
                        <span className={`knowledge-sync-target-status is-${privacyMode}`}>
                          {privacyMode}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
              <div className="knowledge-sync-workspace-picker">
                <span className="knowledge-sync-url-label">Remote source workspaces</span>
                {remoteWorkspaceProfiles.length ? (
                  <div className="knowledge-sync-workspace-chip-row">
                    {remoteWorkspaceProfiles.map((profile) => {
                      const privacyMode = workspacePrivacyLabel(profile);
                      return (
                        <label
                          key={`remote-source-${profile.id}`}
                          className="knowledge-sync-scope-toggle"
                          title={getWorkspacePrivacyTooltip(privacyMode)}
                        >
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
                          <span className={`knowledge-sync-target-status is-${privacyMode}`}>
                            {privacyMode}
                          </span>
                        </label>
                      );
                    })}
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
            <strong title={SCOPE_ROW_HELP}>Allowed scopes</strong>
            {DEVICE_SCOPE_OPTIONS.map((scope) => (
              <label key={scope} className="knowledge-sync-scope-toggle" title={DEVICE_SCOPE_HELP[scope]}>
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
              tooltip={PAIRING_CODE_HELP}
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
            <button
              type="button"
              className="icon-btn"
              onClick={upsertSavedPeer}
              disabled={savingPrefs || remoteAddressDirty}
              title={remoteAddressDirty ? "Verify the changed address before saving it." : undefined}
            >
              {savingPrefs
                ? "Saving..."
                : selectedPeerId
                  ? "Save connection settings"
                  : "Save address"}
            </button>
            <button type="button" className="icon-btn" onClick={pairWithCode} disabled={pairBusy}>
              {pairBusy ? "Pairing..." : "Pair using code"}
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
              text="Saved devices"
              tooltip="Saved connection addresses and verified pairs. The address is only a reachability hint; the saved fingerprint is the device check."
            />
          </h4>
          <p className="status-note">A saved address is not a pair until a one-time code establishes trust.</p>
        </div>
        {savedPeers.length ? (
          <div className="knowledge-sync-target-list">
            {savedPeers.map((peer) => {
              const peerReachable =
                peer.id === selectedPeerId &&
                !remoteAddressDirty &&
                !!peerStatus?.reachable &&
                syncRemoteUrl.trim() === String(peer.remote_url || "").trim();
              const peerChecked =
                peer.id === selectedPeerId &&
                !remoteAddressDirty &&
                peerStatus !== null &&
                syncRemoteUrl.trim() === String(peer.remote_url || "").trim();
              const peerState = describePeerStatus(peer, {
                checked: peerChecked,
                identityVerified: peerChecked && !!peerStatus?.identity_verified,
                reachable: peerReachable,
              });
              return (
                <article
                  key={peer.id}
                  className={`knowledge-sync-target-card ${peer.id === selectedPeerId ? "active" : ""}`}
                >
                  <button
                    type="button"
                    className="knowledge-sync-target-main"
                    onClick={() => selectSavedPeer(peer)}
                  >
                    <div className="knowledge-sync-target-title-row">
                      <strong>{peer.label}</strong>
                      <span className={`knowledge-sync-target-status is-${peerState.key}`}>
                        {peerState.label}
                      </span>
                    </div>
                    <div className="knowledge-sync-preview-chip-row">
                      {normalizePeerScopes(peer.scopes).map((scope) => (
                        <span
                          key={`${peer.id}-${scope}`}
                          className="knowledge-sync-preview-chip"
                          title={DEVICE_SCOPE_HELP[scope]}
                        >
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
                      {shortFingerprint(peer.remote_public_key) ? (
                        <span>fingerprint {shortFingerprint(peer.remote_public_key)}</span>
                      ) : (
                        <span>fingerprint not saved</span>
                      )}
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
                    <button
                      type="button"
                      className="knowledge-sync-target-remove"
                      onClick={() => syncPairTrust(peer)}
                      title={REFRESH_TRUST_HELP}
                    >
                      {pairSyncBusy && selectedPeerId === peer.id ? "Repairing..." : "Repair pairing"}
                    </button>
                    <button
                      type="button"
                      className="knowledge-sync-target-remove"
                      onClick={() => revokeRemotePair(peer)}
                      title={REVOKE_REMOTE_HELP}
                    >
                      Revoke pairing on both devices
                    </button>
                    <button type="button" className="knowledge-sync-target-remove" onClick={() => removeSavedPeer(peer)}>
                      Forget on this device
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
              Compare this device with {remotePreviewLabel} and choose which items to move.
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
          {syncPreview?.data_checkpoint?.summary ? (
            <div className="status-note">
              Data checkpoint: {syncPreview.data_checkpoint.summary}
              {syncPreview.data_checkpoint.last_synced_at
                || syncPreview.data_checkpoint.last_synced_at_iso
                ? ` (${formatDateTime(
                    syncPreview.data_checkpoint.last_synced_at
                      || syncPreview.data_checkpoint.last_synced_at_iso,
                  )})`
                : ""}
            </div>
          ) : null}
          {Array.isArray(syncPreview?.workspaces?.local?.ignored_workspace_ids) &&
          syncPreview.workspaces.local.ignored_workspace_ids.length ? (
            <div className="status-note">
              Ignored local workspaces to avoid recursive sync:{" "}
              {syncPreview.workspaces.local.ignored_workspace_ids
                .map((workspaceId) => workspaceLabel(localWorkspaceProfiles, workspaceId))
                .join(", ")}
            </div>
          ) : null}
          {Array.isArray(syncPreview?.workspaces?.local?.privacy_ignored_workspace_ids) &&
          syncPreview.workspaces.local.privacy_ignored_workspace_ids.length ? (
            <div className="status-note">
              Local workspaces kept private by policy:{" "}
              {syncPreview.workspaces.local.privacy_ignored_workspace_ids
                .map((workspaceId) => workspaceLabel(localWorkspaceProfiles, workspaceId))
                .join(", ")}
            </div>
          ) : null}
          {Array.isArray(syncPreview?.workspaces?.remote?.privacy_ignored_workspace_ids) &&
          syncPreview.workspaces.remote.privacy_ignored_workspace_ids.length ? (
            <div className="status-note">
              Remote workspaces kept private by policy:{" "}
              {syncPreview.workspaces.remote.privacy_ignored_workspace_ids
                .map((workspaceId) => workspaceLabel(remoteWorkspaceProfiles, workspaceId))
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
                <article key={section.key} className="knowledge-sync-preview-card">
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
                </article>
              );
            })}
          </div>
          <div className="knowledge-sync-actions">
            <button
              type="button"
              className="icon-btn"
              onClick={() => applySync("pull")}
              disabled={!previewIsCurrent || !selectedPullItemCount || !!syncActionBusy}
            >
              {syncActionBusy === "pull" ? "Pulling..." : `Apply pull here (${selectedPullItemCount})`}
            </button>
            <button
              type="button"
              className="icon-btn"
              onClick={() => applySync("push")}
              disabled={!previewIsCurrent || !selectedPushItemCount || !!syncActionBusy}
            >
              {syncActionBusy === "push"
                ? "Sending..."
                : `Apply send to ${selectedPeerLabel} (${selectedPushItemCount})`}
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
                    ? `Choose which ${syncItemReviewSection.label.toLowerCase()} to send or delete on ${remotePreviewLabel}.`
                    : `Choose which ${syncItemReviewSection.label.toLowerCase()} to pull or delete here from ${remotePreviewLabel}.`}
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
            {syncItemReviewDeletionIds.length ? (
              <p className="status-note">
                Deletions are off by default. {syncItemReviewSelectedDeletionCount}/
                {syncItemReviewDeletionIds.length} selected explicitly.
              </p>
            ) : null}
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
                    <span
                      className="knowledge-sync-preview-item-status"
                      title={syncItemIsDeletion(item, syncItemReviewDirection) ? "Deletion requires explicit selection." : undefined}
                    >
                      <strong>{syncProposedActionLabel(item, syncItemReviewDirection)}</strong>
                      <span>{syncPreviewStatusLabel(item?.status)}</span>
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
                        className="icon-btn knowledge-sync-action--warning"
                        onClick={() => revertSyncAction(action)}
                        disabled={undoSyncBusyId === action.id}
                        title="Revert the local changes recorded for this sync action."
                      >
                        {undoSyncBusyId === action.id ? "Undoing..." : "Undo local sync"}
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
                {reviewBusyId === review.id && reviewBusyAction === "approve" ? (
                  <p className="status-note" role="status">
                    Applying sync from {review.source_label || "remote device"}. This can take a
                    moment while local indexes refresh.
                  </p>
                ) : null}
                <div className="knowledge-sync-actions">
                  <button
                    type="button"
                    className="icon-btn knowledge-sync-action--primary"
                    onClick={() => approvePendingReview(review)}
                    disabled={reviewBusyId === review.id}
                    title="Approve and apply this incoming push."
                  >
                    {reviewBusyId === review.id && reviewBusyAction === "approve" ? "Applying..." : "Approve"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn knowledge-sync-action--danger"
                    onClick={() => rejectPendingReview(review)}
                    disabled={reviewBusyId === review.id}
                    title="Reject this incoming push without applying it."
                  >
                    {reviewBusyId === review.id && reviewBusyAction === "reject" ? "Rejecting..." : "Reject"}
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
        {trustedInboundDevices.length ? (
          <div className="knowledge-sync-device-list">
            {trustedInboundDevices.map((device) => (
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
                  <button
                    type="button"
                    className="icon-btn knowledge-sync-action--danger"
                    onClick={() => revokeInboundDevice(device)}
                    title={`Remove local trust for ${device.name}.`}
                    aria-label={`Revoke trust for ${device.name}`}
                  >
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
        {visibleLegacyInboundDevices.length ? (
          <div className="knowledge-sync-card-subtle">
            <div className="knowledge-sync-section-head">
              <div className="knowledge-sync-section-stack">
                <strong>Unverified legacy records</strong>
                <span className="status-note">
                  Older records without one-time pairing proof are not trusted devices. Prune them when they are no longer needed.
                </span>
              </div>
              <button
                type="button"
                className="icon-btn knowledge-sync-action--danger"
                onClick={pruneLegacyDevices}
                disabled={pruneLegacyBusy}
                title="Remove unverified legacy device records from this instance."
                aria-label={`Prune ${visibleLegacyInboundDevices.length} unverified legacy device ${visibleLegacyInboundDevices.length === 1 ? "record" : "records"}`}
              >
                {pruneLegacyBusy ? "Cleaning..." : `Prune ${visibleLegacyInboundDevices.length}`}
              </button>
            </div>
            <div className="knowledge-sync-device-list compact">
              {visibleLegacyInboundDevices.slice(0, 6).map((device) => (
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
              tooltip="Review local files as conversation transcripts or save them as searchable documents. This is separate from trusted-device sync."
            />
          </h4>
          <p className="status-note">Review conversations and documents before saving, or export the full conversation set here.</p>
        </div>
        <input
          ref={importFileInputRef}
          type="file"
          className="knowledge-sync-hidden-input"
          accept=".zip,.json,.md,.markdown,.txt"
          onChange={handleImportFileChange}
        />
        <div className="knowledge-sync-grid">
          <div className="knowledge-sync-card-subtle">
            <strong>Import</strong>
            <span>Preview files first, then choose conversation history or Documents and knowledge search.</span>
            <button type="button" className="icon-btn" onClick={triggerImportPicker} disabled={importBusy}>
              {importBusy ? "Reviewing..." : "Review file or archive"}
            </button>
            {importStatus ? (
              <span
                className="status-note"
                role={importStatusKind === "error" ? "alert" : "status"}
              >
                {importStatus}
              </span>
            ) : null}
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
          <div className="knowledge-sync-import-review import-review-shell">
            <div className="knowledge-sync-section-head">
              <div className="knowledge-sync-section-stack">
                <strong>Import review</strong>
                {!classifiedImport ? (
                  <span className="status-note">{importReview.file.name}</span>
                ) : null}
              </div>
              <button
                type="button"
                className="import-review-button import-review-button--quiet"
                onClick={clearImportReview}
              >
                Clear
              </button>
            </div>
            {classifiedImport ? (
              <>
                <div
                  className={`import-review-card import-review-card--${importReview.classification}`}
                >
                  <div className="import-review-summary">
                    <strong className="import-review-title">
                      {classifiedImportDescription?.title}
                    </strong>
                    <span className="import-review-detail" role="status">
                      {classifiedImportDescription?.detail}
                    </span>
                    <div className="import-review-meta">
                      <span className="import-review-filename">{importReview.file.name}</span>
                      {classifiedRoleSummary ? (
                        <span className="import-review-count">{classifiedRoleSummary}</span>
                      ) : null}
                    </div>
                  </div>
                  {(importReview.warnings || []).map((warning, index) => (
                    <span
                      key={`sync-import-warning-${index}`}
                      className="import-review-warning"
                      role="alert"
                    >
                      <strong>Warning:</strong> {warning}
                    </span>
                  ))}
                  {importReview.preview ? (
                    <pre className="import-review-preview" aria-label="File import preview">
                      {importReview.preview}
                    </pre>
                  ) : null}
                </div>
                {canImportRecognizedConversation ? (
                  <>
                    <label className="field-label" htmlFor="sync-import-folder">
                      <span>Conversation destination folder</span>
                    </label>
                    <input
                      id="sync-import-folder"
                      type="text"
                      value={importReview.destinationFolder}
                      onChange={(event) =>
                        setImportReview((prev) => ({ ...prev, destinationFolder: event.target.value }))
                      }
                      placeholder="Leave blank for root"
                    />
                  </>
                ) : null}
                <div className="import-review-actions">
                  {canImportRecognizedConversation && !conversationImportIsPrimary ? (
                    <button
                      type="button"
                      className="import-review-button import-review-button--secondary"
                      onClick={() =>
                        uploadConversationImport({
                          file: importReview.file,
                          destinationFolder: importReview.destinationFolder.trim(),
                          intent: "conversation",
                          confirmAmbiguous: importReview.classification === "ambiguous",
                        })
                      }
                      disabled={importBusy}
                    >
                      {importReview.classification === "ambiguous"
                        ? "Import recognized messages"
                        : "Import conversation"}
                    </button>
                  ) : null}
                  {canSaveClassifiedDocument ? (
                    <button
                      type="button"
                      className={`import-review-button ${
                        conversationImportIsPrimary
                          ? "import-review-button--secondary"
                          : "import-review-button--primary"
                      }`}
                      onClick={() => uploadDocumentImport({ file: importReview.file })}
                      disabled={importBusy}
                    >
                      Save as document
                    </button>
                  ) : null}
                  {canImportRecognizedConversation && conversationImportIsPrimary ? (
                    <button
                      type="button"
                      className="import-review-button import-review-button--primary"
                      onClick={() =>
                        uploadConversationImport({
                          file: importReview.file,
                          destinationFolder: importReview.destinationFolder.trim(),
                          intent: "conversation",
                        })
                      }
                      disabled={importBusy}
                    >
                      Import conversation
                    </button>
                  ) : null}
                </div>
              </>
            ) : (
              <>
                <label className="field-label" htmlFor="sync-import-folder">
                  <span>Destination folder</span>
                </label>
                <input
                  id="sync-import-folder"
                  type="text"
                  value={importReview.destinationFolder}
                  onChange={(event) =>
                    setImportReview((prev) => ({ ...prev, destinationFolder: event.target.value }))
                  }
                  placeholder="Leave blank for root"
                />
                <div className="knowledge-sync-section-head">
                  <span className="status-note">
                    Detected files ({importReviewSelectedCount}/{importReview.detectedFiles.length})
                  </span>
                  <button
                    type="button"
                    className="import-review-button import-review-button--secondary"
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
                {importReviewIgnoredJsonCount > 0 ? (
                  <span className="status-note">
                    Ignored {importReviewIgnoredJsonCount} metadata-only JSON
                    {importReviewIgnoredJsonCount === 1
                      ? ` ${importReviewIgnoredJsonLabel.slice(0, -1)}`
                      : ` ${importReviewIgnoredJsonLabel}`}
                    .
                  </span>
                ) : null}
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
                  className="import-review-button import-review-button--primary"
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
              </>
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
};

export default KnowledgeSyncTab;
