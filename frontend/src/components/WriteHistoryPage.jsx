import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import axios from "axios";
import ActionHistoryPanel from "./ActionHistoryPanel";
import "../styles/Settings.css";
import "../styles/WorkHistoryPage.css";

const RUN_PAGE_SIZE = 100;

const metadataText = (value, fallback = "Not recorded") => {
  if (value === null || value === undefined || value === "") return fallback;
  if (!["string", "number", "boolean"].includes(typeof value)) return fallback;
  return String(value).slice(0, 400);
};

const formatMetadata = (value, fallback) =>
  metadataText(value, fallback).replace(/_/g, " ");

const stateDeltaMessage = (certainty) => {
  const normalized = String(certainty || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  if (["unknown", "uncertain", "unconfirmed"].includes(normalized)) {
    return "State changes unknown; reconciliation required.";
  }
  if (["reported_success", "acknowledged"].includes(normalized)) {
    return "The tool reported success; external state was not independently verified.";
  }
  if (
    [
      "none",
      "no_change",
      "no_changes",
      "unchanged",
      "confirmed_no_change",
      "confirmed_unchanged",
      "no_change_since_checkpoint",
    ].includes(normalized)
  ) {
    return "No other durable state changes were recorded.";
  }
  return "";
};

const emptyRunSummary = (status) => {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "authorization_required") {
    return "Waiting for authorization; no tool or external effect has run.";
  }
  if (normalized === "prompt_resume_pending") {
    return "Prompt recovery is queued from its durable checkpoint.";
  }
  if (
    [
      "cancel_requested",
      "claimed",
      "followup_pending",
      "in_progress",
      "pending",
      "queued",
      "retrying",
      "running",
    ].includes(normalized)
  ) {
    return "Run is still in progress; no text summary has been recorded yet.";
  }
  if (
    [
      "abandoned",
      "error",
      "failed",
      "interrupted_unknown",
      "orphaned",
      "timed_out",
      "timeout",
      "unknown",
    ].includes(normalized)
  ) {
    return "Run ended without a text summary; inspect its evidence and recovery state.";
  }
  return "No text summary was recorded.";
};

const evidenceLimitCopy = (shown, total, hasMore, label) => {
  if (!hasMore && shown >= total) return "";
  if (total > shown) return `Showing first ${shown} of ${total} ${label}.`;
  return `Showing first ${shown} ${label}; more are recorded.`;
};

const authorizationScopes = (value) => {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((scope) => String(scope || "").trim()).filter(Boolean))];
};

const authorizationErrorMessage = (error, decision) => {
  const detail = error?.response?.data?.detail || error?.response?.data?.message;
  const status = Number(error?.response?.status || 0);
  const fallback =
    decision === "approve_once"
      ? "Approval could not be recorded."
      : "This occurrence could not be skipped.";
  if (status === 409) {
    return `${typeof detail === "string" && detail.trim() ? detail.trim() : "This review changed elsewhere."} Refresh Activity and review the current state before trying again.`;
  }
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (typeof error?.message === "string" && error.message.trim()) {
    return `${fallback} ${error.message.trim()}`;
  }
  return fallback;
};

const reconciliationErrorMessage = (error) => {
  const detail = error?.response?.data?.detail || error?.response?.data?.message;
  const status = Number(error?.response?.status || 0);
  if (status === 409) {
    return `${typeof detail === "string" && detail.trim() ? detail.trim() : "This effect was reconciled elsewhere."} Refresh Activity and review the current evidence before trying again.`;
  }
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return "The reconciliation decision could not be recorded. The effect remains unresolved.";
};

const UNRESOLVED_EFFECT_STATUSES = new Set([
  "dispatched",
  "interrupted_unknown",
  "unknown",
]);
const UNCERTAIN_EFFECT_STATES = new Set(["uncertain", "unconfirmed", "unknown"]);

const effectNeedsReconciliation = (effect) => {
  if (!effect || typeof effect !== "object") return false;
  const status = String(effect.status || "").trim().toLowerCase();
  return effect.reconcile_required === true || UNRESOLVED_EFFECT_STATUSES.has(status);
};

const runNeedsEffectReview = (run, details) => {
  if (!run || typeof run !== "object") return false;
  const status = String(run.status || "").trim().toLowerCase();
  const effectStatus = String(run.effect_status || "").trim().toLowerCase();
  const certainty = String(
    run.effect_certainty || run.state_delta_certainty || "",
  )
    .trim()
    .toLowerCase();
  if (
    run.reconcile_required === true ||
    UNRESOLVED_EFFECT_STATUSES.has(status) ||
    UNRESOLVED_EFFECT_STATUSES.has(effectStatus) ||
    UNCERTAIN_EFFECT_STATES.has(certainty)
  ) {
    return true;
  }
  return Array.isArray(details?.effects) && details.effects.some(effectNeedsReconciliation);
};

const stopRequestErrorMessage = (error) => {
  const detail = error?.response?.data?.detail || error?.response?.data?.message;
  const status = Number(error?.response?.status || 0);
  if (status === 409) {
    return `${typeof detail === "string" && detail.trim() ? detail.trim() : "This run changed before the stop request was recorded."} Refresh Activity and review the current state before trying again.`;
  }
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return "The stop request could not be recorded. This run may still be active.";
};

const apiWarningMessage = (value) => {
  if (typeof value === "string") return value.trim().slice(0, 600);
  if (!Array.isArray(value)) return "";
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean)
    .join(" ")
    .slice(0, 600);
};

const WriteHistoryPage = ({
  actions = [],
  backendReady = true,
  loading = false,
  onRefresh,
  userTimezone,
}) => {
  const [searchParams] = useSearchParams();
  const jobFilter = searchParams.get("job_id") || "";
  const [activeTab, setActiveTab] = useState("runs");
  const [runs, setRuns] = useState([]);
  const [runsTotal, setRunsTotal] = useState(0);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState("");
  const [expandedRunIds, setExpandedRunIds] = useState(() => new Set());
  const [runDetails, setRunDetails] = useState({});
  const [authorizationPendingKey, setAuthorizationPendingKey] = useState("");
  const [authorizationErrors, setAuthorizationErrors] = useState({});
  const [reconciliationPendingKey, setReconciliationPendingKey] = useState("");
  const [reconciliationErrors, setReconciliationErrors] = useState({});
  const [reconciliationWarnings, setReconciliationWarnings] = useState({});
  const [stopRequestStates, setStopRequestStates] = useState({});
  const [stopRequestErrors, setStopRequestErrors] = useState({});
  const [stopRequestWarnings, setStopRequestWarnings] = useState({});
  const detailRequestVersion = useRef(0);

  const loadRuns = useCallback(
    async (offset = 0) => {
      if (!backendReady) return;
      setRunsLoading(true);
      setRunsError("");
      try {
        const params = { limit: RUN_PAGE_SIZE, offset };
        if (jobFilter) params.job_id = jobFilter;
        const response = await axios.get("/api/work/runs", { params });
        const nextRuns = Array.isArray(response?.data?.runs) ? response.data.runs : [];
        setRuns((current) => (offset ? [...current, ...nextRuns] : nextRuns));
        setRunsTotal(Number(response?.data?.count) || nextRuns.length);
      } catch (error) {
        console.error("Failed to load background run history", error);
        setRunsError("Run history is unavailable right now.");
      } finally {
        setRunsLoading(false);
      }
    },
    [backendReady, jobFilter],
  );

  useEffect(() => {
    detailRequestVersion.current += 1;
    setExpandedRunIds(new Set());
    setRunDetails({});
    loadRuns(0);
  }, [loadRuns]);

  const refreshAll = () => {
    onRefresh?.();
    detailRequestVersion.current += 1;
    setExpandedRunIds(new Set());
    setRunDetails({});
    setAuthorizationErrors({});
    setReconciliationErrors({});
    setReconciliationWarnings({});
    setStopRequestStates({});
    setStopRequestErrors({});
    setStopRequestWarnings({});
    loadRuns(0);
  };

  const submitAuthorizationDecision = async (run, decision) => {
    const authorization =
      run?.authorization && typeof run.authorization === "object" ? run.authorization : {};
    const eventId = String(run?.event_id || authorization.event_id || "").trim();
    const actionId = String(run?.action_id || authorization.action_id || "").trim();
    const authorizationId = String(authorization.id || "").trim();
    const requestDigest = String(authorization.request_digest || "").trim();
    const occurrenceAt = run?.occurrence_at ?? authorization.occurrence_at;
    const runKey = String(run?.id || `${eventId}:${actionId}`);
    const pendingKey = `${runKey}:${decision}`;
    if (!eventId || !actionId || !authorizationId || !requestDigest || occurrenceAt == null) {
      setAuthorizationErrors((current) => ({
        ...current,
        [runKey]: "Authorization evidence is incomplete. Open Calendar and review the scheduled action.",
      }));
      return;
    }

    setAuthorizationPendingKey(pendingKey);
    setAuthorizationErrors((current) => ({ ...current, [runKey]: "" }));
    try {
      await axios.post(
        `/api/calendar/events/${encodeURIComponent(eventId)}/actions/${encodeURIComponent(actionId)}/authorization`,
        {
          decision,
          authorization_id: authorizationId,
          request_digest: requestDigest,
          occurrence_at: occurrenceAt,
        },
      );
      await loadRuns(0);
    } catch (error) {
      setAuthorizationErrors((current) => ({
        ...current,
        [runKey]: authorizationErrorMessage(error, decision),
      }));
    } finally {
      setAuthorizationPendingKey("");
    }
  };

  const submitEffectReconciliation = async (run, effect, decision, effectKey) => {
    const runId = String(run?.id || "").trim();
    const receiptId = String(effect?.receipt_id || run?.receipt_id || runId).trim();
    const effectId = String(effect?.id || effect?.effect_id || "").trim();
    if (!receiptId || !effectId) {
      setReconciliationErrors((current) => ({
        ...current,
        [effectKey]: "Reconciliation evidence is incomplete. Refresh Activity before deciding.",
      }));
      return;
    }

    setReconciliationPendingKey(effectKey);
    setReconciliationErrors((current) => ({ ...current, [effectKey]: "" }));
    setReconciliationWarnings((current) => ({ ...current, [effectKey]: "" }));
    try {
      const response = await axios.post(
        `/api/work/runs/${encodeURIComponent(receiptId)}/effects/${encodeURIComponent(effectId)}/reconcile`,
        { decision },
      );
      const resolvedEffect =
        response?.data?.effect && typeof response.data.effect === "object"
          ? response.data.effect
          : {
              ...effect,
              status: "confirmed",
              certainty:
                decision === "confirm_applied"
                  ? "user_confirmed_applied"
                  : "user_confirmed_no_change",
              reconcile_required: false,
              reconciliation_decision: decision,
            };
      setRunDetails((current) => {
        const currentDetails = current[runId];
        if (!currentDetails) return current;
        return {
          ...current,
          [runId]: {
            ...currentDetails,
            effects: currentDetails.effects.map((item) =>
              String(item?.id || item?.effect_id || "") === effectId
                ? { ...item, ...resolvedEffect }
                : item,
            ),
          },
        };
      });
      setReconciliationWarnings((current) => ({
        ...current,
        [effectKey]: apiWarningMessage(response?.data?.warning),
      }));
      await loadRuns(0);
    } catch (error) {
      setReconciliationErrors((current) => ({
        ...current,
        [effectKey]: reconciliationErrorMessage(error),
      }));
    } finally {
      setReconciliationPendingKey("");
    }
  };

  const submitStopRequest = async (run, runKey) => {
    const eventId = String(run?.event_id || "").trim();
    const actionId = String(run?.action_id || "").trim();
    const runId = String(run?.run_id || "").trim();
    if (!eventId || !actionId || !runId) {
      setStopRequestErrors((current) => ({
        ...current,
        [runKey]: "Active-run evidence is incomplete. Refresh Activity before requesting a stop.",
      }));
      return;
    }

    setStopRequestStates((current) => ({ ...current, [runKey]: "pending" }));
    setStopRequestErrors((current) => ({ ...current, [runKey]: "" }));
    setStopRequestWarnings((current) => ({ ...current, [runKey]: "" }));
    try {
      const response = await axios.post(
        `/api/calendar/events/${encodeURIComponent(eventId)}/actions/${encodeURIComponent(actionId)}/cancel`,
        { run_id: runId },
      );
      setStopRequestStates((current) => ({ ...current, [runKey]: "requested" }));
      setStopRequestWarnings((current) => ({
        ...current,
        [runKey]: apiWarningMessage(response?.data?.warning),
      }));
      await loadRuns(0);
    } catch (error) {
      setStopRequestStates((current) => ({ ...current, [runKey]: "" }));
      setStopRequestErrors((current) => ({
        ...current,
        [runKey]: stopRequestErrorMessage(error),
      }));
    }
  };

  const toggleRunDetails = async (run) => {
    const runId = String(run?.id || "");
    if (!runId) return;
    const isExpanded = expandedRunIds.has(runId);
    setExpandedRunIds((current) => {
      const next = new Set(current);
      if (isExpanded) next.delete(runId);
      else next.add(runId);
      return next;
    });
    if (isExpanded || runDetails[runId]) return;

    setRunDetails((current) => ({
      ...current,
      [runId]: {
        events: [],
        attempts: [],
        effects: [],
        errors: {},
        loading: true,
      },
    }));
    const requestVersion = detailRequestVersion.current;
    const encodedRunId = encodeURIComponent(runId);
    const requestOptions = { params: { limit: 100, offset: 0 } };
    const [eventsResult, attemptsResult, effectsResult] = await Promise.allSettled([
      axios.get(`/api/work/runs/${encodedRunId}/events`, requestOptions),
      axios.get(`/api/work/runs/${encodedRunId}/attempts`, requestOptions),
      axios.get(`/api/work/runs/${encodedRunId}/effects`, requestOptions),
    ]);
    if (requestVersion !== detailRequestVersion.current) return;

    const unpack = (result, key, fallbackCount, error) => {
      if (result.status !== "fulfilled") {
        console.error(error);
        return { count: fallbackCount, error, hasMore: false, items: [] };
      }
      const data = result.value?.data || {};
      const items = Array.isArray(data[key]) ? data[key] : [];
      const responseCount = Number(data.count);
      const count = Number.isFinite(responseCount) ? responseCount : items.length;
      return {
        count,
        error: "",
        hasMore: Boolean(data.has_more) || count > items.length,
        items,
      };
    };
    const events = unpack(
      eventsResult,
      "events",
      Number(run.event_count || 0),
      "Lifecycle transitions are unavailable right now.",
    );
    const attempts = unpack(
      attemptsResult,
      "attempts",
      Number(run.attempt_count || 0),
      "Provider attempts are unavailable right now.",
    );
    const effects = unpack(
      effectsResult,
      "effects",
      Number(run.effect_count || 0),
      "Effect evidence is unavailable right now.",
    );
    setRunDetails((current) => ({
      ...current,
      [runId]: {
        events: events.items,
        attempts: attempts.items,
        effects: effects.items,
        eventCount: events.count,
        attemptCount: attempts.count,
        effectCount: effects.count,
        eventHasMore: events.hasMore,
        attemptHasMore: attempts.hasMore,
        effectHasMore: effects.hasMore,
        errors: {
          events: events.error,
          attempts: attempts.error,
          effects: effects.error,
        },
        loading: false,
      },
    }));
  };

  const formatOwner = (run, ownership) => {
    const lineage = [];
    const calendarEventId = run.event_id || ownership.calendar_event_id;
    if (calendarEventId) lineage.push(`Calendar ${calendarEventId}`);
    if (ownership.conversation_id) lineage.push(`Chat ${ownership.conversation_id}`);
    if (ownership.message_id) lineage.push(`Message ${ownership.message_id}`);
    if (ownership.parent_job_id) lineage.push(`Parent job ${ownership.parent_job_id}`);
    if (ownership.parent_agent_id) lineage.push(`Parent agent ${ownership.parent_agent_id}`);
    if (lineage.length) return lineage.join(" / ");
    if (run.source === "reflection") return "Manual reflection";
    return ownership.owner_kind ? String(ownership.owner_kind).replace(/_/g, " ") : "Manual";
  };

  const formatStatus = (status) => {
    const normalized = String(status || "complete").toLowerCase();
    if (["invoked", "prompted"].includes(normalized)) return "complete";
    if (normalized === "error") return "failed";
    return normalized.replace(/_/g, " ");
  };
  const displayTimezone =
    (typeof userTimezone === "string" && userTimezone.trim()) ||
    Intl.DateTimeFormat().resolvedOptions().timeZone ||
    "UTC";
  const runCountLabel =
    runsTotal > runs.length ? `${runs.length} of ${runsTotal}` : String(runs.length);
  const reviewRuns = runs.filter((run) => {
    const status = String(run?.status || "").trim().toLowerCase();
    return (
      status === "authorization_required" ||
      runNeedsEffectReview(run, runDetails[String(run?.id || "")])
    );
  });
  const visibleRuns = activeTab === "review" ? reviewRuns : runs;

  return (
    <div className="work-history-page settings-container">
      <section className="settings-card" aria-label="Activity page">
        <div className="settings-card-header">
          <div>
            <h2>Activity</h2>
            <p className="settings-card-copy">
              Calendar and reflection receipts live in a durable device-local ledger without
              turning each run into a conversation. They remain available when a Calendar
              event is deleted; reversible writes stay in their own view.
            </p>
          </div>
          <div className="inline-flex work-history-page-actions">
            <Link
              to="/knowledge?tab=calendar"
              className="icon-btn work-history-page-link work-history-page-action work-history-page-action--primary"
              style={{ marginTop: 0 }}
            >
              Open calendar
            </Link>
            <Link
              to="/settings"
              className="icon-btn work-history-page-link work-history-page-action work-history-page-action--secondary"
              style={{ marginTop: 0 }}
            >
              Back to settings
            </Link>
            <button
              type="button"
              className="icon-btn work-history-page-action work-history-page-action--quiet"
              onClick={refreshAll}
              disabled={!backendReady || loading || runsLoading}
              style={{ marginTop: 0 }}
            >
              {loading || runsLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>

        <div className="activity-tabs" role="tablist" aria-label="Activity views">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "runs"}
            className={activeTab === "runs" ? "active" : ""}
            onClick={() => setActiveTab("runs")}
          >
            Runs ({runCountLabel})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "review"}
            className={activeTab === "review" ? "active activity-review-tab" : "activity-review-tab"}
            onClick={() => setActiveTab("review")}
          >
            Needs review ({reviewRuns.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "writes"}
            className={activeTab === "writes" ? "active" : ""}
            onClick={() => setActiveTab("writes")}
          >
            Writes ({Array.isArray(actions) ? actions.length : 0})
          </button>
        </div>

        {activeTab !== "writes" ? (
          <div className="settings-section activity-run-section" role="tabpanel">
            <div className="activity-surface-note">
              <strong>
                {activeTab === "review"
                  ? "Review work that needs your confirmation."
                  : "Calendar defines future work."}
              </strong>
              <span>
                {activeTab === "review"
                  ? "Authorization cards are paused before dispatch. Reconciliation cards ask you to record what you verified after dispatch; they never retry the effect."
                  : "Agent Console shows current-session work. This list records attempts and available effect evidence."}
                {jobFilter ? ` Filtered to job ${jobFilter}.` : ""} Times use {displayTimezone}.{" "}
                Run evidence contains status, retry, and effect metadata; never prompts, raw
                errors, arguments, or results.
              </span>
            </div>
            {runsError ? <p className="status-note error">{runsError}</p> : null}
            {!runsLoading && !visibleRuns.length && !runsError ? (
              <p className="status-note">
                {activeTab === "review"
                  ? "No work needs authorization or effect confirmation."
                  : "No Calendar or reflection runs are recorded yet."}
              </p>
            ) : null}
            <div className="activity-run-list">
              {visibleRuns.map((run) => {
                const finishedAt = Number(run.finished_at || run.started_at || 0);
                const ownership = run.ownership || {};
                const ownerLabel = formatOwner(run, ownership);
                const statusLabel = formatStatus(run.status);
                const needsAuthorization =
                  String(run.status || "").trim().toLowerCase() ===
                  "authorization_required";
                const authorization =
                  run.authorization && typeof run.authorization === "object"
                    ? run.authorization
                    : {};
                const requiredScopes = authorizationScopes(authorization.required_scopes);
                const missingScopes = authorizationScopes(authorization.missing_scopes);
                const authorizationId = String(authorization.id || "").trim();
                const requestDigest = String(authorization.request_digest || "").trim();
                const occurrenceAt = run.occurrence_at ?? authorization.occurrence_at;
                const eventId = String(run.event_id || authorization.event_id || "").trim();
                const actionId = String(run.action_id || authorization.action_id || "").trim();
                const runKey = String(run.id || `${eventId}:${actionId}`);
                const authorizationMetadataReady = Boolean(
                  authorizationId && requestDigest && eventId && actionId && occurrenceAt != null,
                );
                const canApprove =
                  authorization.can_approve === true && authorizationMetadataReady;
                const approvalBusy = authorizationPendingKey === `${runKey}:approve_once`;
                const denyBusy = authorizationPendingKey === `${runKey}:deny`;
                const authorizationBusy = approvalBusy || denyBusy;
                const authorizationError = authorizationErrors[runKey] || "";
                const calendarHref = eventId
                  ? `/knowledge?tab=calendar&event_id=${encodeURIComponent(eventId)}`
                  : "/knowledge?tab=calendar";
                const recoveryCount = Number(run.recovery_count || 0);
                const recoveryState = String(run.recovery_state || "").toLowerCase();
                const phaseLabel = run.phase ? formatStatus(run.phase) : "";
                const eventCount = Number(run.event_count || 0);
                const attemptCount = Number(run.attempt_count || 0);
                const effectCount = Number(run.effect_count || 0);
                const detailCounts = [
                  eventCount
                    ? `${eventCount} ${eventCount === 1 ? "transition" : "transitions"}`
                    : "",
                  attemptCount
                    ? `${attemptCount} ${attemptCount === 1 ? "attempt" : "attempts"}`
                    : "",
                  effectCount
                    ? `${effectCount} ${effectCount === 1 ? "effect" : "effects"}`
                    : "",
                ].filter(Boolean);
                const isExpanded = expandedRunIds.has(String(run.id));
                const details = runDetails[String(run.id)];
                const needsEffectReview = runNeedsEffectReview(run, details);
                const normalizedRunStatus = String(run.status || "").trim().toLowerCase();
                const activeCalendarRun = ["followup_running", "running"].includes(
                  normalizedRunStatus,
                );
                const stopRunId = String(run.run_id || "").trim();
                const stopRequestState = stopRequestStates[runKey] || "";
                const stopPending = stopRequestState === "pending";
                const stopRequested =
                  stopRequestState === "requested" ||
                  run.cancel_requested === true ||
                  ["cancel_requested", "stop_requested"].includes(normalizedRunStatus);
                const canRequestStop = Boolean(
                  activeCalendarRun &&
                    eventId &&
                    actionId &&
                    stopRunId &&
                    !needsEffectReview &&
                    !stopRequested,
                );
                const stopRequestError = stopRequestErrors[runKey] || "";
                const stopRequestWarning = stopRequestWarnings[runKey] || "";
                const attentionLabel = needsAuthorization
                  ? "Authorization needs attention"
                  : needsEffectReview
                    ? "Effect needs reconciliation"
                    : "Needs attention";
                return (
                  <article
                    className={`activity-run-card${
                      recoveryState === "attention" ? " needs-attention" : ""
                    }${needsAuthorization || needsEffectReview ? " needs-review" : ""}`}
                    key={runKey}
                  >
                    <div className="activity-run-card-header">
                      <div>
                        <strong>{run.event_title || run.event_id || "Background run"}</strong>
                        <span>{run.action_name || run.action_kind || "prompt"}</span>
                      </div>
                      <span className={`activity-run-status status-${statusLabel}`}>
                        {statusLabel}
                      </span>
                    </div>
                    {phaseLabel || recoveryCount || recoveryState === "attention" ? (
                      <div className="activity-run-badges" aria-label="Recovery state">
                        {phaseLabel && phaseLabel !== statusLabel ? (
                          <span>Phase: {phaseLabel}</span>
                        ) : null}
                        {recoveryCount ? (
                          <span>
                            Recovered {recoveryCount === 1 ? "once" : `${recoveryCount} times`}
                          </span>
                        ) : null}
                        {recoveryState === "attention" ? (
                          <span className="attention">{attentionLabel}</span>
                        ) : null}
                      </div>
                    ) : null}
                    <p>{run.summary || emptyRunSummary(run.status)}</p>
                    {needsAuthorization ? (
                      <section
                        className="activity-authorization-review"
                        aria-label={`Authorization review for ${
                          run.event_title || run.event_id || "scheduled work"
                        }`}
                        aria-busy={authorizationBusy}
                      >
                        <div className="activity-authorization-heading">
                          <div>
                            <strong>Approval required — nothing ran</strong>
                            <span>
                              Float stopped before dispatch. Approval applies to this occurrence
                              once.
                            </span>
                          </div>
                          <span className="activity-authorization-state">Needs review</span>
                        </div>
                        <dl className="activity-authorization-scopes">
                          <dt>Required scopes</dt>
                          <dd>
                            {requiredScopes.length ? requiredScopes.join(", ") : "None recorded"}
                          </dd>
                          <dt>Missing scopes</dt>
                          <dd>
                            {missingScopes.length ? missingScopes.join(", ") : "None"}
                          </dd>
                        </dl>
                        {!canApprove ? (
                          <p className="activity-authorization-note">
                            This request cannot be approved from Activity. Open Calendar to review
                            the scheduled action.
                          </p>
                        ) : null}
                        <div className="activity-authorization-actions">
                          <button
                            type="button"
                            className="activity-authorization-approve"
                            disabled={!canApprove || authorizationBusy}
                            title={
                              canApprove
                                ? "Allow this scheduled action for this occurrence only"
                                : "Open Calendar to review why this action cannot be approved here"
                            }
                            onClick={() => submitAuthorizationDecision(run, "approve_once")}
                          >
                            {approvalBusy ? "Approving..." : "Approve and allow once"}
                          </button>
                          <button
                            type="button"
                            className="activity-authorization-skip"
                            disabled={!authorizationMetadataReady || authorizationBusy}
                            onClick={() => submitAuthorizationDecision(run, "deny")}
                          >
                            {denyBusy ? "Skipping..." : "Skip this occurrence"}
                          </button>
                          <Link className="activity-authorization-calendar-link" to={calendarHref}>
                            Open calendar
                          </Link>
                        </div>
                        {authorizationError ? (
                          <p className="activity-authorization-error" role="alert">
                            {authorizationError}
                          </p>
                        ) : null}
                      </section>
                    ) : null}
                    {canRequestStop || stopRequested ? (
                      <section
                        className="activity-stop-request"
                        aria-label={`Stop request for ${
                          run.event_title || run.event_id || "Calendar work"
                        }`}
                        aria-busy={stopPending}
                      >
                        <div>
                          <strong role={stopRequested ? "status" : undefined}>
                            {stopRequested ? "Stop requested" : "Running Calendar work"}
                          </strong>
                          <span>
                            Float will stop before dispatch when possible. Already-dispatched
                            non-cooperative work may still finish and require reconciliation.
                          </span>
                        </div>
                        {canRequestStop ? (
                          <button
                            type="button"
                            disabled={stopPending}
                            title="Float will stop before dispatch when possible; already-dispatched non-cooperative work may still finish and require reconciliation"
                            onClick={() => submitStopRequest(run, runKey)}
                          >
                            Request stop
                          </button>
                        ) : null}
                        {stopRequestError ? (
                          <p className="activity-stop-request-error" role="alert">
                            {stopRequestError}
                          </p>
                        ) : null}
                        {stopRequestWarning ? (
                          <p className="activity-operation-warning" role="status">
                            {stopRequestWarning}
                          </p>
                        ) : null}
                      </section>
                    ) : null}
                    <div className="activity-run-meta">
                      <span title="The schedule or chat that owns this job">{ownerLabel}</span>
                      {finishedAt ? (
                        <time dateTime={new Date(finishedAt * 1000).toISOString()}>
                          {new Date(finishedAt * 1000).toLocaleString([], {
                            timeZone: displayTimezone,
                          })}
                        </time>
                      ) : null}
                      {run.occurrence_at ? (
                        <span title="Scheduled occurrence">
                          Occurrence{" "}
                          {new Date(run.occurrence_at * 1000).toLocaleString([], {
                            timeZone: displayTimezone,
                          })}
                        </span>
                      ) : null}
                    </div>
                    {detailCounts.length ? (
                      <div className="activity-run-lifecycle">
                        <button
                          type="button"
                          className="activity-run-lifecycle-toggle"
                          aria-expanded={isExpanded}
                          onClick={() => toggleRunDetails(run)}
                        >
                          {isExpanded ? "Hide" : "Show"} {detailCounts.join(", ")}
                        </button>
                        {isExpanded ? (
                          <div className="activity-run-lifecycle-detail">
                            {details?.loading ? <span>Loading run evidence...</span> : null}
                            {details &&
                            !details.loading &&
                            (details.events.length ||
                              details.eventCount ||
                              details.errors?.events) ? (
                              <section aria-label="Lifecycle transitions">
                                <h3>Lifecycle</h3>
                                {details.errors?.events ? (
                                  <p className="activity-evidence-error">
                                    {details.errors.events}
                                  </p>
                                ) : null}
                                {details.events.length ? (
                                  <ol>
                                    {details.events.map((event) => {
                                      const recordedAt = Number(event.recorded_at || 0);
                                      const transition = formatStatus(
                                        event.phase || event.followup_status || event.status,
                                      );
                                      return (
                                        <li key={event.sequence}>
                                          <strong>{transition}</strong>
                                          {event.recovery_reason_code ? (
                                            <span> / {event.recovery_reason_code}</span>
                                          ) : null}
                                          {recordedAt ? (
                                            <time
                                              dateTime={new Date(
                                                recordedAt * 1000,
                                              ).toISOString()}
                                            >
                                              {new Date(recordedAt * 1000).toLocaleString([], {
                                                timeZone: displayTimezone,
                                              })}
                                            </time>
                                          ) : null}
                                        </li>
                                      );
                                    })}
                                  </ol>
                                ) : null}
                                {evidenceLimitCopy(
                                  details.events.length,
                                  details.eventCount,
                                  details.eventHasMore,
                                  "transitions",
                                ) ? (
                                  <p className="activity-evidence-limit">
                                    {evidenceLimitCopy(
                                      details.events.length,
                                      details.eventCount,
                                      details.eventHasMore,
                                      "transitions",
                                    )}
                                  </p>
                                ) : null}
                              </section>
                            ) : null}
                            {details &&
                            !details.loading &&
                            (details.attempts.length ||
                              details.attemptCount ||
                              details.errors?.attempts) ? (
                              <section aria-label="Provider attempts">
                                <h3>Provider attempts</h3>
                                {details.errors?.attempts ? (
                                  <p className="activity-evidence-error">
                                    {details.errors.attempts}
                                  </p>
                                ) : null}
                                {details.attempts.length ? (
                                  <ol className="activity-evidence-list">
                                    {details.attempts.map((attempt, index) => {
                                    const attemptNumber =
                                      attempt.attempt_number || attempt.number || index + 1;
                                    const certainty =
                                      attempt.state_delta_certainty ||
                                      attempt.state_change_certainty ||
                                      attempt.state_delta?.certainty ||
                                      attempt.certainty;
                                    const retryNote =
                                      attempt.retry_note ||
                                      attempt.retry_reason_code ||
                                      attempt.retry?.reason_code;
                                    const deltaMessage = stateDeltaMessage(certainty);
                                    return (
                                      <li key={attempt.id || attemptNumber}>
                                        <div className="activity-evidence-heading">
                                          <strong>Provider attempt {attemptNumber}</strong>
                                          <span>{formatMetadata(attempt.status, "unknown")}</span>
                                        </div>
                                        <dl>
                                          {attempt.provider ? (
                                            <>
                                              <dt>Provider</dt>
                                              <dd>{metadataText(attempt.provider)}</dd>
                                            </>
                                          ) : null}
                                          <dt>Error category</dt>
                                          <dd>
                                            {formatMetadata(
                                              attempt.error_category,
                                              "No provider error recorded",
                                            )}
                                          </dd>
                                          <dt>Retry note</dt>
                                          <dd>
                                            {formatMetadata(
                                              retryNote,
                                              "No retry note recorded",
                                            )}
                                          </dd>
                                          <dt>State-delta certainty</dt>
                                          <dd>{formatMetadata(certainty, "not recorded")}</dd>
                                        </dl>
                                        {deltaMessage ? (
                                          <p
                                            className={`activity-state-delta ${
                                              String(certainty).toLowerCase() === "unknown"
                                                ? "attention"
                                                : ""
                                            }`}
                                          >
                                            {deltaMessage}
                                          </p>
                                        ) : null}
                                      </li>
                                    );
                                    })}
                                  </ol>
                                ) : null}
                                {evidenceLimitCopy(
                                  details.attempts.length,
                                  details.attemptCount,
                                  details.attemptHasMore,
                                  "attempts",
                                ) ? (
                                  <p className="activity-evidence-limit">
                                    {evidenceLimitCopy(
                                      details.attempts.length,
                                      details.attemptCount,
                                      details.attemptHasMore,
                                      "attempts",
                                    )}
                                  </p>
                                ) : null}
                              </section>
                            ) : null}
                            {details &&
                            !details.loading &&
                            (details.effects.length ||
                              details.effectCount ||
                              details.errors?.effects) ? (
                              <section aria-label="External effects">
                                <h3>Effects</h3>
                                {details.errors?.effects ? (
                                  <p className="activity-evidence-error">
                                    {details.errors.effects}
                                  </p>
                                ) : null}
                                {details.effects.length ? (
                                  <ol className="activity-evidence-list">
                                     {details.effects.map((effect, index) => {
                                     const certainty =
                                       effect.certainty || effect.state_delta_certainty;
                                     const effectId = String(
                                       effect.id || effect.effect_id || "",
                                     ).trim();
                                     const receiptId = String(
                                       effect.receipt_id || run.receipt_id || run.id || "",
                                     ).trim();
                                     const toolLabel = metadataText(
                                       effect.tool_name || effect.tool,
                                       "External effect",
                                     );
                                     const needsReconciliation = effectNeedsReconciliation(effect);
                                     const effectKey = `${receiptId || run.id || "run"}:${
                                       effectId || index
                                     }`;
                                     const reconciliationMetadataReady = Boolean(
                                       receiptId && effectId,
                                     );
                                     const reconciliationBusy =
                                       reconciliationPendingKey === effectKey;
                                     const reconciliationError =
                                       reconciliationErrors[effectKey] || "";
                                     const reconciliationWarning =
                                       reconciliationWarnings[effectKey] || "";
                                     const permission =
                                       effect.permission_snapshot || effect.permission || {};
                                    const approval =
                                      effect.approval_snapshot || effect.approval || {};
                                    const deltaMessage = stateDeltaMessage(certainty);
                                    return (
                                      <li key={effect.id || effect.effect_id || index}>
                                        <div className="activity-evidence-heading">
                                           <strong>
                                             {toolLabel}
                                           </strong>
                                          <span>{formatMetadata(effect.status, "unknown")}</span>
                                        </div>
                                        <dl>
                                          <dt>Scope</dt>
                                          <dd>
                                            {formatMetadata(
                                              effect.effect_scope || effect.scope,
                                              "Not recorded",
                                            )}
                                          </dd>
                                          <dt>Certainty</dt>
                                          <dd>{formatMetadata(certainty, "not recorded")}</dd>
                                          <dt>Replay policy</dt>
                                          <dd>
                                            {formatMetadata(
                                              effect.replay_policy,
                                              "Manual review required",
                                            )}
                                          </dd>
                                          {permission.status ? (
                                            <>
                                              <dt>Permission evidence</dt>
                                              <dd
                                                title="Declared records the scopes requested for this run; the server enforces its granted permission snapshot before dispatch."
                                              >
                                                {formatMetadata(permission.status, "Not recorded")}
                                              </dd>
                                            </>
                                          ) : null}
                                          {approval.required || approval.status ? (
                                            <>
                                              <dt>Approval evidence</dt>
                                              <dd>
                                                {approval.required ? "Required / " : ""}
                                                {formatMetadata(approval.status, "not recorded")}
                                              </dd>
                                            </>
                                          ) : null}
                                        </dl>
                                         {deltaMessage ? (
                                          <p
                                            className={`activity-state-delta ${
                                              String(certainty).toLowerCase() === "unknown"
                                                ? "attention"
                                                : ""
                                            }`}
                                          >
                                             {deltaMessage}
                                           </p>
                                         ) : null}
                                         {needsReconciliation ? (
                                           <section
                                             className="activity-effect-reconciliation"
                                             aria-label={`Reconciliation for ${toolLabel}`}
                                             aria-busy={reconciliationBusy}
                                           >
                                             <strong>External result needs confirmation</strong>
                                             <p>
                                               Float cannot tell whether this change reached the
                                               external system. Record only what you verified.
                                             </p>
                                             {!reconciliationMetadataReady ? (
                                               <p className="activity-effect-reconciliation-note">
                                                 Receipt evidence is incomplete. Refresh Activity
                                                 before deciding.
                                               </p>
                                             ) : null}
                                             <div className="activity-effect-reconciliation-actions">
                                               <button
                                                 type="button"
                                                 disabled={
                                                   !reconciliationMetadataReady ||
                                                   reconciliationBusy
                                                 }
                                                 title="Record that you verified this external change was applied"
                                                 onClick={() =>
                                                   submitEffectReconciliation(
                                                     run,
                                                     effect,
                                                     "confirm_applied",
                                                     effectKey,
                                                   )
                                                 }
                                               >
                                                 It was applied
                                               </button>
                                               <button
                                                 type="button"
                                                 disabled={
                                                   !reconciliationMetadataReady ||
                                                   reconciliationBusy
                                                 }
                                                 title="Record that you verified this external change did not happen"
                                                 onClick={() =>
                                                   submitEffectReconciliation(
                                                     run,
                                                     effect,
                                                     "confirm_no_change",
                                                     effectKey,
                                                   )
                                                 }
                                               >
                                                 No change happened
                                               </button>
                                             </div>
                                             {reconciliationError ? (
                                               <p
                                                 className="activity-effect-reconciliation-error"
                                                 role="alert"
                                               >
                                                 {reconciliationError}
                                               </p>
                                             ) : null}
                                           </section>
                                         ) : null}
                                         {reconciliationWarning ? (
                                           <p className="activity-operation-warning" role="status">
                                             {reconciliationWarning}
                                           </p>
                                         ) : null}
                                       </li>
                                    );
                                    })}
                                  </ol>
                                ) : null}
                                {evidenceLimitCopy(
                                  details.effects.length,
                                  details.effectCount,
                                  details.effectHasMore,
                                  "effects",
                                ) ? (
                                  <p className="activity-evidence-limit">
                                    {evidenceLimitCopy(
                                      details.effects.length,
                                      details.effectCount,
                                      details.effectHasMore,
                                      "effects",
                                    )}
                                  </p>
                                ) : null}
                              </section>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
            {runs.length < runsTotal ? (
              <button
                type="button"
                className="icon-btn activity-load-older"
                disabled={runsLoading}
                onClick={() => loadRuns(runs.length)}
              >
                {runsLoading ? "Loading..." : `Load older runs (${runsTotal - runs.length})`}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="settings-section" role="tabpanel">
            {!loading && (!Array.isArray(actions) || actions.length === 0) ? (
              <p className="status-note">No tracked writes are cached right now.</p>
            ) : null}
            <ActionHistoryPanel
              actions={actions}
              backendReady={backendReady}
              onRefresh={onRefresh}
            />
          </div>
        )}
      </section>
    </div>
  );
};

export default WriteHistoryPage;
