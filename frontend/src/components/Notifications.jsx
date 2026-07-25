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
const TOOL_REVIEW_RECENT_MAX_AGE_MS = 45000;
const ACCEPT_CONTINUE_BATCH_DELAY_MS = 250;
const STANDARD_TOAST_DISMISS_MS = 6000;
const OPERATION_PROGRESS_COMPLETE_DISMISS_MS = 2600;
const OPERATION_PROGRESS_ERROR_DISMISS_MS = 6000;
const OPERATION_PROGRESS_STALE_DISMISS_MS = 120000;
const OPERATION_PROGRESS_TICK_MS = 1000;
const RECENT_COMPLETE_PROGRESS_MAX_AGE_MS = 8000;
const RUNTIME_RAG_OPERATION_EVENT = "float:runtime-rag-operation";
const MAX_TERMINAL_TOOL_REVIEW_IDS = 500;
const TERMINAL_TOOL_REVIEW_STATUSES = new Set([
  "cancelled",
  "canceled",
  "complete",
  "completed",
  "denied",
  "error",
  "failed",
  "forbidden",
  "invoked",
  "ok",
  "rejected",
  "scheduled",
  "succeeded",
  "success",
  "timeout",
  "timed_out",
]);

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

const isToolReviewPayload = (payload) => {
  if (!payload || payload.category !== "tool_resolution") return false;
  return toolReviewItems(payload.data || {}).length > 0;
};

const normalizeToolReviewStatus = (value) =>
  String(value || "").trim().toLowerCase();

const toolReviewPayloadItems = (payload) => {
  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  const fallbackStatus = normalizeToolReviewStatus(data.status);
  return toolReviewItems(data).map((item) => ({
    ...item,
    status: normalizeToolReviewStatus(item.status) || fallbackStatus,
  }));
};

const isTerminalToolReviewItem = (item) =>
  TERMINAL_TOOL_REVIEW_STATUSES.has(normalizeToolReviewStatus(item?.status));

const toolReviewDataForItems = (data, items) => {
  const source = data && typeof data === "object" ? data : {};
  const toolIds = items.map((item) => item.toolId || "");
  const toolNames = items.map((item) => item.toolName || "");
  const toolArgs = items.map((item) => item.args);
  const toolStatuses = items.map((item) => normalizeToolReviewStatus(item.status));
  const selectedToolId = String(
    source.selectedToolId ?? source.selected_tool_id ?? "",
  ).trim();
  const nextSelectedToolId = toolIds.includes(selectedToolId)
    ? selectedToolId
    : toolIds.find(Boolean) || "";
  return {
    ...source,
    toolIds,
    tool_ids: toolIds,
    toolNames,
    tool_names: toolNames,
    toolArgs,
    tool_args: toolArgs,
    toolStatuses,
    tool_statuses: toolStatuses,
    selectedToolId: nextSelectedToolId,
    selected_tool_id: nextSelectedToolId,
  };
};

const toolReviewToastMatchesItems = (toast, items) => {
  if (!isToolReviewToast(toast) || !items.length) return false;
  const incomingIds = new Set(items.map((item) => item.toolId).filter(Boolean));
  if (!incomingIds.size) return false;
  return toolReviewItems(toast.data || {}).some((item) => incomingIds.has(item.toolId));
};

const toolReviewToastId = (payload) => {
  if (!isToolReviewPayload(payload)) return "";
  const target = normalizeToolReviewTarget(payload.data || {});
  const parts = [
    target.sessionId,
    target.messageId || target.chainId,
    ...target.toolIds,
  ].filter(Boolean);
  return parts.length ? `tool-review:${parts.join("|")}` : "";
};

const dismissDelayForToast = (toast) =>
  isToolReviewToast(toast) ? null : STANDARD_TOAST_DISMISS_MS;

const formatToolReviewArgs = (value) => {
  if (typeof value === "undefined") return "";
  if (typeof value === "string") return value.trim();
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? "");
  }
};

const formatToolReviewArgValue = (value) => {
  if (value === null || typeof value === "undefined") return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const toolReviewArgRows = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value)
    .map(([key, raw]) => ({
      key,
      value: formatToolReviewArgValue(raw),
    }))
    .filter((row) => row.key && row.value);
};

const uniqueNonEmptyStrings = (values) => {
  const seen = new Set();
  const result = [];
  values.forEach((value) => {
    const str = String(value ?? "").trim();
    if (!str || seen.has(str)) return;
    seen.add(str);
    result.push(str);
  });
  return result;
};

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

const isRuntimeRagOperationPayload = (payload) => {
  if (!isOperationProgressPayload(payload)) return false;
  const data = payload.data || {};
  const operationId = String(data.operation_id || "").trim().toLowerCase();
  const kind = String(data.kind || "").trim().toLowerCase();
  return kind === "rag_query" && operationId.startsWith("rag-query:");
};

const dispatchRuntimeRagOperation = (toast) => {
  if (typeof window === "undefined" || !toast) return;
  window.dispatchEvent(
    new CustomEvent(RUNTIME_RAG_OPERATION_EVENT, {
      detail: toast,
    }),
  );
};

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
  if (isToolReviewPayload(entry)) {
    const timestampMs = Number(entry?.ts) * 1000;
    if (!Number.isFinite(timestampMs) || timestampMs <= 0) return false;
    return Date.now() - timestampMs <= TOOL_REVIEW_RECENT_MAX_AGE_MS;
  }
  if (!isOperationProgressPayload(entry)) return true;
  const status = String(entry?.data?.status || "").trim().toLowerCase();
  if (status !== "complete") return true;
  const timestampMs = Number(entry?.ts) * 1000;
  if (!Number.isFinite(timestampMs) || timestampMs <= 0) return true;
  return Date.now() - timestampMs <= RECENT_COMPLETE_PROGRESS_MAX_AGE_MS;
};

const Notifications = ({ onOpenToolReview, autoMinimize = false }) => {
  const [toasts, setToasts] = useState([]);
  const [nowMs, setNowMs] = useState(Date.now());
  const [minimized, setMinimized] = useState(Boolean(autoMinimize));
  const [activeShortcut, setActiveShortcut] = useState(null);
  const [activeReviewKey, setActiveReviewKey] = useState(null);
  const [selectedReviewKeysByToast, setSelectedReviewKeysByToast] = useState({});
  const esRef = useRef(null);
  const terminalToolReviewIdsRef = useRef(new Set());
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
    setSelectedReviewKeysByToast((prev) => {
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    clearToastTimer(id);
  }, [clearToastTimer]);

  const scheduleToastDismiss = useCallback(
    (id, delayMs = STANDARD_TOAST_DISMISS_MS) => {
      clearToastTimer(id);
      if (!Number.isFinite(delayMs) || delayMs <= 0) return;
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
          scheduleToastDismiss(toast.id, OPERATION_PROGRESS_STALE_DISMISS_MS);
        }
        return;
      }
      scheduleToastDismiss(toast.id, dismissDelayForToast(toast));
    },
    [clearToastTimer, scheduleToastDismiss],
  );

  const addToast = useCallback((payload) => {
    const progressPayload = isOperationProgressPayload(payload);
    const toolReviewPayload = isToolReviewPayload(payload);
    const incomingToolItems = toolReviewPayload ? toolReviewPayloadItems(payload) : [];
    const terminalToolIds = new Set(
      incomingToolItems
        .filter(isTerminalToolReviewItem)
        .map((item) => item.toolId)
        .filter(Boolean),
    );
    terminalToolIds.forEach((toolId) => terminalToolReviewIdsRef.current.add(toolId));
    while (terminalToolReviewIdsRef.current.size > MAX_TERMINAL_TOOL_REVIEW_IDS) {
      const oldestToolId = terminalToolReviewIdsRef.current.values().next().value;
      terminalToolReviewIdsRef.current.delete(oldestToolId);
    }
    const unresolvedToolItems = incomingToolItems.filter(
      (item) =>
        !isTerminalToolReviewItem(item) &&
        (!item.toolId || !terminalToolReviewIdsRef.current.has(item.toolId)),
    );
    const activeToolReviewPayload = toolReviewPayload && unresolvedToolItems.length > 0;
    const activeToolReviewData = activeToolReviewPayload
      ? toolReviewDataForItems(payload?.data, unresolvedToolItems)
      : payload?.data || {};
    const activeToolReviewEnvelope = activeToolReviewPayload
      ? { ...payload, data: activeToolReviewData }
      : payload;
    const id = progressPayload
      ? operationToastId(payload?.data?.operation_id)
      : activeToolReviewPayload
        ? toolReviewToastId(activeToolReviewEnvelope) ||
          `${payload?.ts || Date.now()}-${Math.random().toString(36).slice(2)}`
      : `${payload?.ts || Date.now()}-${Math.random().toString(36).slice(2)}`;
    const nextToast = {
      id,
      title: payload?.title,
      body: payload?.body,
      data: activeToolReviewData,
      category: payload?.category || "general",
    };

    if (progressPayload && isRuntimeRagOperationPayload(payload)) {
      dispatchRuntimeRagOperation(nextToast);
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
      clearToastTimer(id);
      return;
    }

    setToasts((prev) => {
      let reconciled = prev;
      if (terminalToolIds.size) {
        reconciled = prev.flatMap((toast) => {
          if (!isToolReviewToast(toast)) return [toast];
          const remainingItems = toolReviewItems(toast.data || {}).filter(
            (item) => !terminalToolIds.has(item.toolId),
          );
          if (!remainingItems.length) return [];
          if (remainingItems.length === toolReviewItems(toast.data || {}).length) {
            return [toast];
          }
          return [
            {
              ...toast,
              data: toolReviewDataForItems(toast.data, remainingItems),
            },
          ];
        });
      }

      if (toolReviewPayload && !activeToolReviewPayload) {
        return reconciled;
      }
      if (!progressPayload && !activeToolReviewPayload) {
        return [...reconciled, nextToast];
      }
      const existingIndex = reconciled.findIndex(
        (toast) =>
          toast.id === id ||
          (activeToolReviewPayload && toolReviewToastMatchesItems(toast, unresolvedToolItems)),
      );
      if (existingIndex < 0) {
        return [...reconciled, nextToast];
      }
      const merged = {
        ...reconciled[existingIndex],
        ...nextToast,
        id: reconciled[existingIndex].id,
        ...(terminalToolIds.size
          ? {
              title: reconciled[existingIndex].title,
              body: reconciled[existingIndex].body,
            }
          : {}),
        data: {
          ...(reconciled[existingIndex]?.data || {}),
          ...activeToolReviewData,
        },
      };
      return reconciled.map((toast, index) => (index === existingIndex ? merged : toast));
    });

    if (toolReviewPayload && !activeToolReviewPayload) {
      return;
    }
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
      scheduleToastDismiss(id, OPERATION_PROGRESS_STALE_DISMISS_MS);
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
  const hasErrorToast = useMemo(
    () =>
      toasts.some((toast) => {
        const status = String(toast?.data?.status || "").trim().toLowerCase();
        const severity = String(toast?.data?.severity || "").trim().toLowerCase();
        const category = String(toast?.category || "").trim().toLowerCase();
        return (
          status === "error" ||
          status === "failed" ||
          severity === "error" ||
          category === "error"
        );
      }),
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

  const getToastReviewOptions = useCallback(
    (toast) => {
      if (!toast?.id) return [];
      return reviewOptions.filter((option) => option.toastId === toast.id);
    },
    [reviewOptions],
  );

  const getSelectedReviewKeys = useCallback(
    (toast) => {
      const toastOptions = getToastReviewOptions(toast);
      if (!toastOptions.length) return [];
      const validKeys = new Set(toastOptions.map((option) => option.key));
      const storedKeys = Array.isArray(selectedReviewKeysByToast[toast.id])
        ? selectedReviewKeysByToast[toast.id].filter((key) => validKeys.has(key))
        : [];
      if (storedKeys.length) return storedKeys;
      const activeOption = toastOptions.find((option) => option.key === activeReviewKey);
      return [activeOption?.key || toastOptions[0].key];
    },
    [activeReviewKey, getToastReviewOptions, selectedReviewKeysByToast],
  );

  const getSelectedReviewOption = useCallback(
    (toast) => {
      const toastOptions = getToastReviewOptions(toast);
      if (!toastOptions.length) return null;
      const selectedKeys = new Set(getSelectedReviewKeys(toast));
      return (
        toastOptions.find((option) => option.key === activeReviewKey) ||
        toastOptions.find((option) => selectedKeys.has(option.key)) ||
        toastOptions[0]
      );
    },
    [activeReviewKey, getSelectedReviewKeys, getToastReviewOptions],
  );

  const getSelectedReviewOptions = useCallback(
    (toast) => {
      const toastOptions = getToastReviewOptions(toast);
      if (!toastOptions.length) return [];
      const selectedKeys = new Set(getSelectedReviewKeys(toast));
      return toastOptions.filter((option) => selectedKeys.has(option.key));
    },
    [getSelectedReviewKeys, getToastReviewOptions],
  );

  const selectReviewOption = useCallback((option, options = {}) => {
    if (!option?.key || !option.toastId) return;
    setActiveReviewKey(option.key);
    setSelectedReviewKeysByToast((prev) => {
      const stored = Array.isArray(prev[option.toastId]) ? prev[option.toastId] : [];
      const current =
        stored.length || !options.additive || !activeReviewKey?.startsWith(`${option.toastId}:`)
          ? stored
          : [activeReviewKey];
      let nextKeys;
      if (options.additive) {
        if (current.includes(option.key)) {
          nextKeys = current.length > 1 ? current.filter((key) => key !== option.key) : current;
        } else {
          nextKeys = [...current, option.key];
        }
      } else {
        nextKeys = [option.key];
      }
      if (
        current.length === nextKeys.length &&
        current.every((key, index) => key === nextKeys[index])
      ) {
        return prev;
      }
      return {
        ...prev,
        [option.toastId]: nextKeys,
      };
    });
  }, [activeReviewKey]);

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

  const openSelectedToolReview = useCallback(
    (toast, selectedOption = null, options = {}) => {
      const selectedItem = selectedOption?.item || null;
      openToolReview(toast, {
        ...options,
        selectedToolId:
          options.selectedToolId ||
          selectedItem?.toolId ||
          selectedItem?.id ||
          undefined,
      });
      if (options.dismiss) {
        dismissToast(toast.id);
      }
    },
    [dismissToast, openToolReview],
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
      selectReviewOption(next);
      return next;
    },
    [activeReviewKey, reviewOptions, selectReviewOption],
  );

  const runToolReviewAction = useCallback(
    (toast, rawAction, options = {}) => {
      const action = normalizeToolReviewAction(rawAction);
      if (!toast || !action || !isToolReviewToast(toast)) return;
      const target = normalizeToolReviewTarget(toast.data || {});
      const selectedOption = options.selectedOption || getSelectedReviewOption(toast);
      const selectedOptions = Array.isArray(options.selectedOptions) && options.selectedOptions.length
        ? options.selectedOptions
        : getSelectedReviewOptions(toast);
      const scope = options.scope === "batch" ? "batch" : "selected";
      const toolIds =
        scope === "batch"
          ? target.toolIds
          : uniqueNonEmptyStrings(
              (selectedOptions.length ? selectedOptions : [selectedOption]).map(
                (option) => option?.item?.toolId || option?.item?.id || target.selectedToolId,
              ),
            );
      const selectedToolId =
        scope === "batch" ? "" : toolIds[0] || target.selectedToolId || "";
      const keepOpenAfterAction =
        options.keepOpen === true ||
        (target.toolIds.length > 1 && scope === "selected" && action !== "continue");
      flashShortcut(toast.id, options.flashAction || action);
      openToolReview(toast, {
        navigate: action === "edit",
        selectedToolId: selectedToolId || undefined,
      });
      let handled = false;
      const tryDispatch = () => {
        if (handled) return;
        if (scope === "selected" && action !== "edit" && toolIds.length > 1) {
          handled = toolIds.reduce((handledAny, toolId) => {
            const detail = dispatchToolReviewAction(action, {
              ...target,
              toolIds: [toolId],
              selectedToolId: toolId,
              scope,
              notificationId: toast.id,
            });
            return handledAny || Boolean(detail?.handled);
          }, false);
        } else {
          const detail = dispatchToolReviewAction(action, {
            ...target,
            toolIds,
            selectedToolId,
            scope,
            notificationId: toast.id,
          });
          handled = Boolean(detail?.handled);
        }
        if (handled && action !== "edit" && !keepOpenAfterAction) {
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
    [
      dismissToast,
      flashShortcut,
      getSelectedReviewOption,
      getSelectedReviewOptions,
      openToolReview,
    ],
  );

  const runAcceptContinueSelected = useCallback(
    (toast, selectedOption = null) => {
      if (!toast || !isToolReviewToast(toast)) return;
      const toolId = String(selectedOption?.item?.toolId || "").trim();
      if (!toolId) return;
      const target = normalizeToolReviewTarget(toast.data || {});
      const item = selectedOption.item;
      const exactSelection = { toast, toastId: toast.id, item };
      pauseToastDismiss(toast.id);
      runToolReviewAction(toast, "accept", {
        selectedOption: exactSelection,
        selectedOptions: [exactSelection],
        keepOpen: true,
        flashAction: "accept_continue",
      });
      const continueTimer = setTimeout(() => {
        actionTimersRef.current.delete(continueTimer);
        runToolReviewAction(toast, "continue", {
          scope: "selected",
          selectedOption: exactSelection,
          selectedOptions: [exactSelection],
          keepOpen: target.toolIds.length > 1,
          flashAction: "accept_continue",
        });
      }, ACCEPT_CONTINUE_BATCH_DELAY_MS);
      actionTimersRef.current.add(continueTimer);
    },
    [pauseToastDismiss, runToolReviewAction],
  );

  const runAcceptContinueBatch = useCallback(
    (toast, selectedOption = null) => {
      if (!toast || !isToolReviewToast(toast)) return;
      const target = normalizeToolReviewTarget(toast.data || {});
      const items = toolReviewItems(toast.data || {});
      const targetIds = target.toolIds.length
        ? target.toolIds
        : [selectedOption?.item?.toolId || target.toolId].filter(Boolean);
      if (!targetIds.length) return;
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
          selectedOptions: [{ toast, toastId: toast.id, item }],
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
    } catch (err) {
      void err;
    }
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
      } catch (err) {
        void err;
      }
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
    if (hasErrorToast) {
      setMinimized(false);
      return;
    }
    if (autoMinimize) {
      setMinimized(true);
    } else {
      setMinimized(false);
    }
  }, [autoMinimize, hasErrorToast]);

  useEffect(() => {
    if (minimized || !toasts.some(isToolReviewToast)) return undefined;
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
        runAcceptContinueSelected(activeOption.toast, activeOption);
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
    runAcceptContinueSelected,
    runToolReviewAction,
    minimized,
    toasts,
  ]);

  if (toasts.length === 0) return null;

  const operationOnly = toasts.every(isOperationProgressToast);
  const toolReviewCount = reviewOptions.length;
  const activeOperationCount = toasts.filter((toast) => {
    if (!isOperationProgressToast(toast)) return false;
    const status = String(toast.data?.status || "").trim().toLowerCase();
    return status !== "complete" && status !== "error";
  }).length;
  const compactSummary = toolReviewCount
    ? `${toolReviewCount} tool${toolReviewCount === 1 ? "" : "s"} awaiting review`
    : activeOperationCount
      ? `${activeOperationCount} operation${activeOperationCount === 1 ? "" : "s"} running`
      : `${toasts.length} notification${toasts.length === 1 ? "" : "s"}`;

  return (
    <div
      className={`download-tray ${minimized ? "collapsed notification-tray-minimized" : "expanded"} notification-tray${
        operationOnly ? " operation-only" : ""
      }${hasErrorToast ? " has-attention" : ""}`}
      style={{ pointerEvents: "none" }}
    >
      {minimized && (
        <div className="download-tray-rail center-rail notification-tray-rail">
          <button
            type="button"
            className="download-tray-peek notification-tray-peek"
            title={`Show notifications: ${compactSummary}`}
            aria-label={`Show notifications: ${compactSummary}`}
            aria-expanded="false"
            aria-controls="notification-tray-list"
            onClick={() => setMinimized(false)}
          >
            <span
              className="download-tray-peek-icon notification-tray-peek-icon"
              aria-hidden="true"
            >
              {"\u25CF"}
            </span>
            <span className="notification-tray-count" aria-hidden="true">
              {toasts.length}
            </span>
          </button>
        </div>
      )}
      <div
        id="notification-tray-list"
        className="download-tray-content"
        hidden={minimized}
        style={{ pointerEvents: "auto" }}
      >
        <div className="download-tray-header notification-tray-header">
          <button
            type="button"
            className="download-tray-toggle notification-tray-toggle"
            title="Minimize notifications"
            aria-label="Minimize notifications"
            aria-expanded="true"
            onClick={() => setMinimized(true)}
          >
            {"\u2212"}
          </button>
          <div className="download-tray-title">Notifications</div>
          <span className="notification-tray-count" aria-label={`${toasts.length} visible`}>
            {toasts.length}
          </span>
        </div>
        <div className="download-toasts">
          {toasts.map((t) => {
            const toolReview = isToolReviewToast(t);
            const items = toolReview ? toolReviewItems(t.data || {}) : [];
            const selectedOption = getSelectedReviewOption(t);
            const selectedOptions = getSelectedReviewOptions(t);
            const selectedKeys = new Set(selectedOptions.map((option) => option.key));
            const selectedItems = selectedOptions
              .map((option) => option.item)
              .filter(Boolean);
            const selectedItem = selectedOption?.item || items[0] || null;
            const isToolBatch = items.length > 1;
            const selectedCount = selectedItems.length || (selectedItem ? 1 : 0);
            const selectedToolLabel = selectedItem?.label || "selected tool";
            const selectedNames = selectedItems
              .map((item) => item.label)
              .filter(Boolean)
              .join(", ");
            const selectedArgsText = formatToolReviewArgs(selectedItem?.args);
            const selectedArgRows = toolReviewArgRows(selectedItem?.args);
            const selectedStatus = String(selectedItem?.status || "").trim();
            const selectedMeta = [
              selectedItem?.toolId ? `request ${selectedItem.toolId}` : "",
              selectedStatus ? `status ${selectedStatus}` : "",
            ].filter(Boolean);
            const acceptLabel = isToolBatch ? "Accept selected" : "Accept";
            const denyLabel = isToolBatch ? "Deny selected" : "Deny";
            const editLabel = isToolBatch ? "Edit selected" : "Edit";
            const acceptContinueLabel = "Accept + continue";
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
                  {toolReview && isToolBatch && (
                    <div className="tool-review-stack" aria-label="Tool review batch">
                      {items.map((item, index) => {
                        const optionKey = `${t.id}:${item.id}`;
                        const option = reviewOptions.find((entry) => entry.key === optionKey);
                        const selected = selectedKeys.has(optionKey);
                        const active = optionKey === selectedOption?.key;
                        return (
                          <button
                            key={optionKey}
                            type="button"
                            className={`tool-review-stack-item${selected ? " selected" : ""}${
                              active ? " active" : ""
                            }`}
                            aria-pressed={selected}
                            onClick={(event) => {
                              if (option) {
                                selectReviewOption(option, {
                                  additive: Boolean(event.ctrlKey || event.metaKey),
                                });
                              } else {
                                setActiveReviewKey(optionKey);
                              }
                              openSelectedToolReview(t, { item }, {
                                selectedToolId: item.toolId || item.id,
                                navigate: false,
                              });
                            }}
                            title={`View ${item.label}; Ctrl-click to add or remove it from selected tools`}
                          >
                            {index + 1}. {item.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                  {toolReview && selectedItem && (
                    <div className="tool-review-detail-card" aria-live="polite">
                      {isToolBatch && selectedCount > 1 && (
                        <div className="tool-review-selection-summary">
                          {selectedCount} selected{selectedNames ? `: ${selectedNames}` : ""}
                        </div>
                      )}
                      <div className="tool-review-detail-header">
                        <strong>{selectedToolLabel}</strong>
                        {selectedMeta.length > 0 && (
                          <span>{selectedMeta.join(" | ")}</span>
                        )}
                      </div>
                      {selectedArgRows.length ? (
                        <dl className="tool-review-arg-list">
                          {selectedArgRows.map((row) => (
                            <React.Fragment key={row.key}>
                              <dt>{row.key}</dt>
                              <dd>{row.value}</dd>
                            </React.Fragment>
                          ))}
                        </dl>
                      ) : selectedArgsText ? (
                        <pre>{selectedArgsText}</pre>
                      ) : (
                        <p>Open or edit this tool to inspect the full request.</p>
                      )}
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
                        title={
                          selectedCount > 1
                            ? `Accept ${selectedCount} selected tools (Y)`
                            : `Accept ${selectedToolLabel} (Y)`
                        }
                        aria-keyshortcuts="Y"
                        onClick={() =>
                          runToolReviewAction(t, "accept", {
                            selectedOption,
                            selectedOptions,
                          })
                        }
                      >
                        {acceptLabel}
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn deny notification-tool-action${shortcutClass("deny")}`}
                        title={
                          selectedCount > 1
                            ? `Deny ${selectedCount} selected tools (N)`
                            : `Deny ${selectedToolLabel} (N)`
                        }
                        aria-keyshortcuts="N"
                        onClick={() =>
                          runToolReviewAction(t, "deny", {
                            selectedOption,
                            selectedOptions,
                          })
                        }
                      >
                        {denyLabel}
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn edit notification-tool-action${shortcutClass("edit")}`}
                        title={`Edit ${selectedToolLabel} (Alt+N)`}
                        aria-keyshortcuts="Alt+N"
                        onClick={() => runToolReviewAction(t, "edit", { selectedOption })}
                      >
                        {editLabel}
                      </button>
                      <button
                        type="button"
                        className={`tool-action-btn continue notification-tool-action accept-continue${shortcutClass("accept_continue")}`}
                        title={`Accept ${selectedToolLabel}, then continue only that tool (Ctrl+Y)`}
                        aria-keyshortcuts="Control+Y"
                        disabled={!selectedItem?.toolId}
                        onClick={() => runAcceptContinueSelected(t, selectedOption)}
                      >
                        {acceptContinueLabel}
                      </button>
                      {isToolBatch && (
                        <button
                          type="button"
                          className="tool-action-btn continue notification-tool-action accept-continue batch"
                          title="Accept every tool in this notification, then continue the whole batch"
                          onClick={() => runAcceptContinueBatch(t, selectedOption)}
                        >
                          Accept all + continue
                        </button>
                      )}
                    </>
                  )}
                  {t.data?.action_url && (
                    toolReview ? (
                      <button
                        type="button"
                        className="dl-btn"
                        title="Open in console"
                        onClick={() =>
                          openSelectedToolReview(t, selectedOption, {
                            navigate: true,
                            dismiss: true,
                          })
                        }
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
                    type="button"
                    className="dl-btn danger"
                    title="Dismiss"
                    aria-label="Dismiss notification"
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
