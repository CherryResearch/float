import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "../styles/ProgressBar.css";
import "../styles/ToolActions.css";
import {
  dispatchToolReviewAction,
  normalizeToolReviewAction,
  normalizeToolReviewTarget,
  toolReviewItems,
} from "../utils/toolReviewActions";

const TOOL_TOAST_DISMISS_DELAY_MS = 500;
const TOOL_REVIEW_TOAST_DISMISS_MS = 45000;
const ACCEPT_CONTINUE_BATCH_DELAY_MS = 250;
const STANDARD_TOAST_DISMISS_MS = 6000;
const OPERATION_PROGRESS_COMPLETE_DISMISS_MS = 2600;
const OPERATION_PROGRESS_ERROR_DISMISS_MS = 6000;
const OPERATION_PROGRESS_TICK_MS = 1000;
const RECENT_COMPLETE_PROGRESS_MAX_AGE_MS = 8000;

const isTypingTarget = (target) => {
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  return (
    tag === "input" ||
    tag === "textarea" ||
    tag === "select" ||
    target.isContentEditable
  );
};

const isToolReviewToast = (toast) => {
  if (!toast || toast.category !== "tool_resolution") return false;
  return toolReviewItems(toast.data || {}).length > 0;
};

const dismissDelayForToast = (toast) =>
  isToolReviewToast(toast) ? TOOL_REVIEW_TOAST_DISMISS_MS : STANDARD_TOAST_DISMISS_MS;

const isOperationProgressToast = (toast) =>
  Boolean(
    toast &&
      toast.category === "operation_progress" &&
      typeof toast.data?.operation_id === "string" &&
      toast.data.operation_id.trim(),
  );

const isOperationProgressPayload = (payload) =>
  Boolean(
    payload &&
      payload.category === "operation_progress" &&
      typeof payload.data?.operation_id === "string" &&
      payload.data.operation_id.trim(),
  );

const operationToastId = (operationId) => `operation:${String(operationId || "").trim()}`;

const parseIsoTimestamp = (value) => {
  const text = String(value || "").trim();
  if (!text) return null;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : null;
};

const formatElapsed = (ms) => {
  const normalizedMs = Math.max(0, Number(ms) || 0);
  if (normalizedMs < 1000) return `${Math.round(normalizedMs)} ms`;
  if (normalizedMs < 10_000) return `${(normalizedMs / 1000).toFixed(1)} s`;
  const totalSeconds = Math.floor(normalizedMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${String(remMinutes).padStart(2, "0")}m`;
};

const shouldHydrateRecentNotification = (entry) => {
  if (!isOperationProgressPayload(entry)) return true;
  const status = String(entry?.data?.status || "").trim().toLowerCase();
  if (status !== "complete") return true;
  const timestampMs = Number(entry?.ts) * 1000;
  if (!Number.isFinite(timestampMs) || timestampMs <= 0) return true;
  return Date.now() - timestampMs <= RECENT_COMPLETE_PROGRESS_MAX_AGE_MS;
};

const Notifications = ({ onOpenToolReview }) => {
  const [toasts, setToasts] = useState([]);
  const [nowMs, setNowMs] = useState(Date.now());
  const [activeShortcut, setActiveShortcut] = useState(null);
  const [activeReviewKey, setActiveReviewKey] = useState(null);
  const esRef = useRef(null);
  const toastTimersRef = useRef(new Map());
  const shortcutTimerRef = useRef(null);
  const actionTimersRef = useRef(new Set());

  const clearToastTimer = useCallback((id) => {
    const timer = toastTimersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      toastTimersRef.current.delete(id);
    }
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    clearToastTimer(id);
  }, [clearToastTimer]);

  const scheduleToastDismiss = useCallback(
    (id, delayMs = STANDARD_TOAST_DISMISS_MS) => {
      clearToastTimer(id);
      const timer = setTimeout(() => {
        dismissToast(id);
      }, delayMs);
      toastTimersRef.current.set(id, timer);
    },
    [clearToastTimer, dismissToast],
  );

  const pauseToastDismiss = useCallback(
    (id) => {
      clearToastTimer(id);
    },
    [clearToastTimer],
  );

  const resumeToastDismiss = useCallback(
    (toast) => {
      if (!toast) return;
      if (isOperationProgressToast(toast)) {
        const status = String(toast.data?.status || "").trim().toLowerCase();
        if (status === "complete") {
          scheduleToastDismiss(toast.id, OPERATION_PROGRESS_COMPLETE_DISMISS_MS);
        } else if (status === "error") {
          scheduleToastDismiss(toast.id, OPERATION_PROGRESS_ERROR_DISMISS_MS);
        } else {
          clearToastTimer(toast.id);
        }
        return;
      }
      scheduleToastDismiss(toast.id, dismissDelayForToast(toast));
    },
    [clearToastTimer, scheduleToastDismiss],
  );

  const addToast = useCallback((payload) => {
    const progressPayload = isOperationProgressPayload(payload);
    const id = progressPayload
      ? operationToastId(payload?.data?.operation_id)
      : `${payload?.ts || Date.now()}-${Math.random().toString(36).slice(2)}`;
    const nextToast = {
      id,
      title: payload?.title,
      body: payload?.body,
      data: payload?.data || {},
      category: payload?.category || "general",
    };
    setToasts((prev) => {
      if (!progressPayload) {
        return [...prev, nextToast];
      }
      const existingIndex = prev.findIndex((toast) => toast.id === id);
      if (existingIndex < 0) {
        return [...prev, nextToast];
      }
      const merged = {
        ...prev[existingIndex],
        ...nextToast,
        data: {
          ...(prev[existingIndex]?.data || {}),
          ...(payload?.data || {}),
        },
      };
      return prev.map((toast, index) => (index === existingIndex ? merged : toast));
    });

    if (!progressPayload) {
      scheduleToastDismiss(id, dismissDelayForToast(nextToast));
      return;
    }

    const status = String(payload?.data?.status || "").trim().toLowerCase();
    if (status === "complete") {
      scheduleToastDismiss(id, OPERATION_PROGRESS_COMPLETE_DISMISS_MS);
    } else if (status === "error") {
      scheduleToastDismiss(id, OPERATION_PROGRESS_ERROR_DISMISS_MS);
    } else {
      clearToastTimer(id);
    }
  }, [clearToastTimer, scheduleToastDismiss]);

  const flashShortcut = useCallback((toastId, action) => {
    setActiveShortcut({ toastId, action });
    if (shortcutTimerRef.current) {
      clearTimeout(shortcutTimerRef.current);
    }
    shortcutTimerRef.current = setTimeout(() => {
      setActiveShortcut(null);
      shortcutTimerRef.current = null;
    }, 650);
  }, []);

  const reviewOptions = useMemo(
    () =>
      toasts.flatMap((toast) =>
        isToolReviewToast(toast)
          ? toolReviewItems(toast.data || {}).map((item) => ({
              toast,
              toastId: toast.id,
              item,
              key: `${toast.id}:${item.id}`,
            }))
          : [],
      ),
    [toasts],
  );

  useEffect(() => {
    if (!reviewOptions.length) {
      setActiveReviewKey(null);
      return;
    }
    if (reviewOptions.some((option) => option.key === activeReviewKey)) return;
    setActiveReviewKey(reviewOptions[0].key);
  }, [activeReviewKey, reviewOptions]);

  const getSelectedReviewOption = useCallback(
    (toast) => {
      const toastOptions = reviewOptions.filter((option) => option.toastId === toast?.id);
      if (!toastOptions.length) return null;
      return (
        toastOptions.find((option) => option.key === activeReviewKey) ||
        toastOptions[0]
      );
    },
    [activeReviewKey, reviewOptions],
  );

  const cycleReviewSelection = useCallback(
    (direction = 1) => {
      if (!reviewOptions.length) return null;
      const currentIndex = Math.max(
        0,
        reviewOptions.findIndex((option) => option.key === activeReviewKey),
      );
      const nextIndex =
        (currentIndex + direction + reviewOptions.length) % reviewOptions.length;
      const next = reviewOptions[nextIndex];
      setActiveReviewKey(next.key);
      return next;
    },
    [activeReviewKey, reviewOptions],
  );

  const openToolReview = useCallback(
    (toast, options = {}) => {
      const target = normalizeToolReviewTarget(toast?.data || {});
      if (typeof onOpenToolReview === "function") {
        onOpenToolReview({
          ...target,
          selectedToolId: options.selectedToolId || target.selectedToolId,
          actionUrl: target.actionUrl,
          navigate: options.navigate !== false,
        });
      }
    },
    [onOpenToolReview],
  );

  const runToolReviewAction = useCallback(
    (toast, rawAction, options = {}) => {
      const action = normalizeToolReviewAction(rawAction);
      if (!toast || !action || !isToolReviewToast(toast)) return;
      const target = normalizeToolReviewTarget(toast.data || {});
      const selectedOption = options.selectedOption || getSelectedReviewOption(toast);
      const scope = options.scope === "batch" ? "batch" : "selected";
      const toolIds =
        scope === "batch"
          ? target.toolIds
          : [selectedOption?.item?.toolId || target.selectedToolId].filter(Boolean);
      const selectedToolId =
        scope === "batch" ? "" : toolIds[0] || target.selectedToolId || "";
      flashShortcut(toast.id, options.flashAction || action);
      openToolReview(toast, {
        navigate: action === "edit",
        selectedToolId: selectedToolId || undefined,
      });
      let handled = false;
      const tryDispatch = () => {
        if (handled) return;
        const detail = dispatchToolReviewAction(action, {
          ...target,
          toolIds,
          selectedToolId,
          scope,
          notificationId: toast.id,
        });
        handled = Boolean(detail?.handled);
        if (handled && action !== "edit" && options.keepOpen !== true) {
          const dismissTimer = setTimeout(() => {
            actionTimersRef.current.delete(dismissTimer);
            dismissToast(toast.id);
          }, TOOL_TOAST_DISMISS_DELAY_MS);
          actionTimersRef.current.add(dismissTimer);
        }
      };
      tryDispatch();
      [150, 450, 900].forEach((delay) => {
        const timer = setTimeout(() => {
          actionTimersRef.current.delete(timer);
          tryDispatch();
        }, delay);
        actionTimersRef.current.add(timer);
      });
    },
    [dismissToast, flashShortcut, getSelectedReviewOption, openToolReview],
  );

  const runAcceptContinueBatch = useCallback(
    (toast, selectedOption = null) => {
      if (!toast || !isToolReviewToast(toast)) return;
      const target = normalizeToolReviewTarget(toast.data || {});
      const items = toolReviewItems(toast.data || {});
      const targetIds = target.toolIds.length
        ? target.toolIds
        : [selectedOption?.item?.toolId || target.selectedToolId || target.toolId].filter(Boolean);
      pauseToastDismiss(toast.id);
      targetIds.forEach((toolId) => {
        const item =
          items.find((candidate) => candidate.toolId === toolId || candidate.id === toolId) || {
            id: toolId,
            toolId,
            label: toolId ? `tool ${String(toolId).slice(0, 8)}` : "selected tool",
          };
        runToolReviewAction(toast, "accept", {
          selectedOption: { toast, toastId: toast.id, item },
          keepOpen: true,
          flashAction: "accept_continue",
        });
      });
      const continueTimer = setTimeout(() => {
        actionTimersRef.current.delete(continueTimer);
        runToolReviewAction(toast, "continue", {
          scope: "batch",
          selectedOption,
          flashAction: "accept_continue",
        });
      }, ACCEPT_CONTINUE_BATCH_DELAY_MS);
      actionTimersRef.current.add(continueTimer);
    },
    [pauseToastDismiss, runToolReviewAction],
  );

  useEffect(() => {
    try {
      fetch("/api/notifications/recent")
        .then((res) => (res.ok ? res.json() : { notifications: [] }))
        .then((payload) => {
          const items = Array.isArray(payload?.notifications)
            ? payload.notifications.slice(-3)
            : [];
          items.filter(shouldHydrateRecentNotification).forEach((entry) => addToast(entry));
        })
        .catch(() => {});
    } catch {}
    if (typeof EventSource !== "function") {
      return undefined;
    }
    const source = new EventSource("/api/stream/notifications");
    const handler = (evt) => {
      try {
        addToast(JSON.parse(evt.data || "{}"));
      } catch {
        // ignore
      }
    };
    source.addEventListener("notification", handler);
    source.onmessage = handler;
    esRef.current = source;
    return () => {
      try {
        source.close();
      } catch {}
    };
  }, [addToast]);

  useEffect(() => {
    const hasActiveProgressToast = toasts.some((toast) => {
      if (!isOperationProgressToast(toast)) return false;
      const status = String(toast.data?.status || "").trim().toLowerCase();
      return status !== "complete" && status !== "error";
    });
    if (!hasActiveProgressToast) return undefined;
    const timer = setInterval(() => {
      setNowMs(Date.now());
    }, OPERATION_PROGRESS_TICK_MS);
    return () => clearInterval(timer);
  }, [toasts]);

  useEffect(() => {
    return () => {
      toastTimersRef.current.forEach((timer) => clearTimeout(timer));
      toastTimersRef.current.clear();
      if (shortcutTimerRef.current) {
        clearTimeout(shortcutTimerRef.current);
      }
      actionTimersRef.current.forEach((timer) => clearTimeout(timer));
      actionTimersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!toasts.some(isToolReviewToast)) return undefined;
    const handleKeyDown = (event) => {
      if (event.defaultPrevented || isTypingTarget(event.target)) return;
      const key = String(event.key || "").toLowerCase();
      if (key === "tab") {
        event.preventDefault();
        event.stopPropagation();
        cycleReviewSelection(event.shiftKey ? -1 : 1);
        return;
      }
      let action = "";
      if (event.altKey && key === "n") {
        action = "edit";
      } else if (event.ctrlKey && !event.altKey && !event.metaKey && key === "y") {
        action = "accept_continue";
      } else if (!event.altKey && !event.ctrlKey && !event.metaKey && key === "y") {
        action = "accept";
      } else if (!event.altKey && !event.ctrlKey && !event.metaKey && key === "n") {
        action = "deny";
      }
      if (!action) return;
      const activeOption =
        reviewOptions.find((option) => option.key === activeReviewKey) ||
        reviewOptions[0];
      if (!activeOption?.toast) return;
      event.preventDefault();
      event.stopPropagation();
      if (action === "accept_continue") {
        runAcceptContinueBatch(activeOption.toast, activeOption);
      } else {
        runToolReviewAction(activeOption.toast, action, {
          selectedOption: activeOption,
        });
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    activeReviewKey,
    cycleReviewSelection,
    reviewOptions,
    runAcceptContinueBatch,
    runToolReviewAction,
    toasts,
  ]);

  if (toasts.length === 0) return null;

  const operationOnly = toasts.every(isOperationProgressToast);

  return (
    <div
      className={`download-tray expanded notification-tray${
        operationOnly ? " operation-only" : ""
      }`}
      style={{ pointerEvents: "none" }}
    >
      <div className="download-tray-content" style={{ pointerEvents: "auto" }}>
        <div className="download-toasts">
          {toasts.map((t) => {
            const toolReview = isToolReviewToast(t);
            const items = toolReview ? toolReviewItems(t.data || {}) : [];
            const selectedOption = getSelectedReviewOption(t);
            const selectedItem = selectedOption?.item || items[0] || null;
            const toastSelected = Boolean(
              selectedOption && selectedOption.key === activeReviewKey,
            );
            const operationProgress = isOperationProgressToast(t);
            const operationStatus = String(t.data?.status || "").trim().toLowerCase();
            const operationPhaseIndex = Number(t.data?.phase_index);
            const operationPhaseCount = Number(t.data?.phase_count);
            const hasPhaseProgress =
              Number.isFinite(operationPhaseIndex) &&
              Number.isFinite(operationPhaseCount) &&
              operationPhaseCount > 0;
            const operationTrackWidth = hasPhaseProgress
              ? `${Math.max(
                  0,
                  Math.min(
                    100,
                    Math.round((operationPhaseIndex / operationPhaseCount) * 100),
                  ),
                )}%`
              : operationStatus === "complete"
                ? "100%"
                : "100%";
            const startedAtMs = parseIsoTimestamp(t.data?.started_at);
            const explicitElapsedMs = Number(t.data?.elapsed_ms);
            const elapsedMs = Number.isFinite(explicitElapsedMs)
              ? explicitElapsedMs
              : startedAtMs
                ? Math.max(0, nowMs - startedAtMs)
                : null;
            const operationDetail = String(t.data?.detail || "").trim();
            const operationPhaseLabel = String(t.data?.phase_label || "").trim();
            const operationMeta = [];
            if (hasPhaseProgress) {
              operationMeta.push(
                `Step ${Math.max(1, operationPhaseIndex)} of ${Math.max(1, operationPhaseCount)}`,
              );
            }
            if (elapsedMs !== null) {
              operationMeta.push(formatElapsed(elapsedMs));
            }
            const shortcutClass = (action) =>
              activeShortcut?.toastId === t.id && activeShortcut?.action === action
                ? " shortcut-active"
                : "";
            return (
              <div
                className={`download-toast${toolReview ? " tool-resolution-toast" : ""}${
                  toastSelected ? " selected-tool-review-toast" : ""
                }${operationProgress ? " operation-progress-toast" : ""}${
                  operationStatus ? ` is-${operationStatus}` : ""
                }`}
                key={t.id}
                onMouseEnter={() => pauseToastDismiss(t.id)}
                onFocus={() => pauseToastDismiss(t.id)}
                onMouseLeave={() => resumeToastDismiss(t)}
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) {
                    resumeToastDismiss(t);
                  }
                }}
              >
                <div className="download-toast-text">
                  <strong>{t.title || "Notification"}</strong>
                  {t.body ? ` \u2014 ${t.body}` : ""}
                  {operationProgress && (
                    <div className="operation-progress-stack">
                      {operationPhaseLabel ? (
                        <div className="operation-progress-detail">{operationPhaseLabel}</div>
                      ) : null}
                      <div
                        className="download-progress-track small operation-progress-track"
                        role="progressbar"
                        aria-label="Operation progress"
                        aria-valuemin={0}
                        aria-valuemax={hasPhaseProgress ? operationPhaseCount : 1}
                        aria-valuenow={
                          hasPhaseProgress
                            ? Math.max(0, Math.min(operationPhaseCount, operationPhaseIndex))
                            : operationStatus === "complete"
                              ? 1
                              : undefined
                        }
                      >
                        <div
                          className={`download-progress-fill operation-progress-fill${
                            !hasPhaseProgress && operationStatus !== "complete"
                              ? " is-indeterminate"
                              : ""
                          }`}
                          style={{ width: operationTrackWidth }}
                        />
                      </div>
                      {(operationMeta.length > 0 || operationDetail) && (
                        <div className="operation-progress-meta">
                          {operationMeta.join(" | ")}
                          {operationMeta.length > 0 && operationDetail ? " | " : ""}
                          {operationDetail}
                        </div>
                      )}
                    </div>
                  )}
                  {toolReview && items.length > 1 && (
                    <div className="tool-review-stack" aria-label="Tool review batch">
                      {items.map((item, index) => {
                        const optionKey = `${t.id}:${item.id}`;
                        const selected = optionKey === selectedOption?.key;
                        return (
                          <button
                            key={optionKey}
                            type="button"
                            className={`tool-review-stack-item${selected ? " selected" : ""}`}
                            onClick={() => setActiveReviewKey(optionKey)}
                            title={`Select ${item.label}`}
                          >
                            {index + 1}. {item.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="download-toast-actions">
                  {operationProgress && (
                    <span className="download-item-meta operation-progress-status">
                      {operationStatus || "running"}
                    </span>
                  )}
                  {toolReview && (
                    <>
                      <button
                        type="button"
                        className={`tool-action-btn accept notification-tool-action${shortcutClass("accept")}`}
                        title={`Accept ${selectedItem?.label || "selected tool"} (Y)`}
                        aria-keyshortcuts="Y"
                        onClick={() => runToolReviewAction(t, "accept", { selectedOption })}
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn deny notification-tool-action${shortcutClass("deny")}`}
                        title={`Deny ${selectedItem?.label || "selected tool"} (N)`}
                        aria-keyshortcuts="N"
                        onClick={() => runToolReviewAction(t, "deny", { selectedOption })}
                      >
                        Deny
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn edit notification-tool-action${shortcutClass("edit")}`}
                        title={`Edit ${selectedItem?.label || "selected tool"} (Alt+N)`}
                        aria-keyshortcuts="Alt+N"
                        onClick={() => runToolReviewAction(t, "edit", { selectedOption })}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn continue notification-tool-action${shortcutClass("continue")}`}
                        title={`Continue with ${selectedItem?.label || "selected tool"}`}
                        onClick={() => runToolReviewAction(t, "continue", { selectedOption })}
                      >
                        Continue
                      </button>
                      {items.length > 1 && (
                        <button
                          type="button"
                          className={`tool-action-btn continue notification-tool-action batch${shortcutClass("accept_continue")}`}
                          title="Accept all tools, then continue the batch (Ctrl+Y)"
                          aria-keyshortcuts="Control+Y"
                          onClick={() => runAcceptContinueBatch(t, selectedOption)}
                        >
                          Accept & continue batch
                        </button>
                      )}
                    </>
                  )}
                  {t.data?.action_url && (
                    toolReview ? (
                      <button
                        type="button"
                        className="dl-btn"
                        title="Open"
                        onClick={() => openToolReview(t)}
                      >
                        Open
                      </button>
                    ) : (
                      <a className="dl-btn" href={t.data.action_url} title="Open">
                        Open
                      </a>
                    )
                  )}
                  {t.data?.path && (
                    <a
                      className="dl-btn"
                      href={`file://${t.data.path}`}
                      target="_blank"
                      rel="noreferrer noopener"
                      title="Open folder"
                    >
                      Open folder
                    </a>
                  )}
                  <button
                    className="dl-btn danger"
                    title="Dismiss"
                    onClick={() => dismissToast(t.id)}
                  >
                    {"\u2715"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default Notifications;
