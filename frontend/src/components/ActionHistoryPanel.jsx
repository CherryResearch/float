import React from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";

const formatTimestamp = (value) => {
  const ts = Number(value);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const conversationLabelFor = (action) => {
  if (action?.conversation_label) return String(action.conversation_label);
  if (action?.conversation_id) return String(action.conversation_id);
  return "workspace changes";
};

const responseLabelFor = (action) => {
  if (action?.response_label) return String(action.response_label);
  if (action?.response_id) {
    const id = String(action.response_id);
    return `response ${id.slice(-8)}`;
  }
  return "outside chat";
};

const ACTION_KIND_LABELS = {
  revert: "undo",
};

const ACTION_NAME_LABELS = {
  revert_actions: "undo",
  sync_ingest: "incoming sync",
  sync_pull: "sync pull",
};

const normalizeActionLabel = (value, fallback) => {
  const text = String(value || "").trim();
  if (!text) return fallback;
  return text.replace(/[_-]+/g, " ");
};

const itemCountFor = (value) => {
  const raw =
    value && typeof value === "object" ? value.item_count : value;
  const count = Number(raw);
  if (!Number.isFinite(count) || count <= 0) return 0;
  return count;
};

const actionKindLabelFor = (action) => {
  const raw = String(action?.kind || "").trim().toLowerCase();
  return ACTION_KIND_LABELS[raw] || normalizeActionLabel(action?.kind, "action");
};

const actionNameLabelFor = (action) => {
  const raw = String(action?.name || "").trim().toLowerCase();
  if (raw && ACTION_NAME_LABELS[raw]) return ACTION_NAME_LABELS[raw];
  if (String(action?.kind || "").trim().toLowerCase() === "revert") return "undo";
  return normalizeActionLabel(action?.name, "write");
};

const actionTitleFor = (action) => {
  const summary = String(action?.summary || "").trim();
  if (summary) return summary;
  return actionNameLabelFor(action) || actionKindLabelFor(action);
};

const actionTimestampFor = (action) =>
  formatTimestamp(action?.created_at_ts || action?.timestamp);

const describeActionReference = (action, actionMap) => {
  if (!action || typeof action !== "object") return null;
  const targetIds = Array.isArray(action.target_action_ids)
    ? action.target_action_ids
        .map((id) => String(id || "").trim())
        .filter(Boolean)
    : [];
  if (targetIds.length) {
    const targets = targetIds.map((id) => actionMap.get(id)).filter(Boolean);
    const restoredCount = itemCountFor(action);
    const targetCount = targets.reduce((sum, target) => sum + itemCountFor(target), 0);
    const reference =
      targets.length === 1
        ? `Undo target: ${[actionTimestampFor(targets[0]), actionTitleFor(targets[0])]
            .filter(Boolean)
            .join(" · ")}`
        : targets.length > 1
          ? `Undo target: ${targets.length} earlier actions`
          : targetIds.length === 1
            ? `Undo target: action ${targetIds[0].slice(-8)}`
            : `Undo target: ${targetIds.length} earlier actions`;
    let detail = "";
    if (restoredCount > 0 && targetCount > 0 && restoredCount < targetCount) {
      detail = `Restored ${restoredCount} of ${targetCount} tracked items. ${
        targetCount - restoredCount
      } already matched the earlier state.`;
    } else if (restoredCount > 0 && targetCount > 0) {
      detail =
        restoredCount >= targetCount
          ? `Restored all ${targetCount} tracked items.`
          : `Restored ${restoredCount} of ${targetCount} tracked items.`;
    } else if (restoredCount > 0) {
      detail = `Restored ${restoredCount} tracked item${restoredCount === 1 ? "" : "s"}.`;
    } else if (targetCount > 0) {
      detail = `${targetCount} tracked item${targetCount === 1 ? "" : "s"} were in scope for this undo.`;
    }
    return { reference, detail };
  }

  const revertedById = String(action.reverted_by_action_id || "").trim();
  if (!revertedById) return null;
  const revertAction = actionMap.get(revertedById);
  const actionCount = itemCountFor(action);
  const restoredCount = itemCountFor(revertAction);
  const reference = revertAction
    ? `Later undo: ${[actionTimestampFor(revertAction), actionTitleFor(revertAction)]
        .filter(Boolean)
        .join(" · ")}`
    : `Later undo: action ${revertedById.slice(-8)}`;
  let detail = "";
  if (restoredCount > 0 && actionCount > 0 && restoredCount < actionCount) {
    detail = `That undo restored ${restoredCount} of ${actionCount} tracked items.`;
  } else if (restoredCount > 0 && actionCount > 0) {
    detail =
      restoredCount >= actionCount
        ? `That undo restored all ${actionCount} tracked items.`
        : `That undo restored ${restoredCount} of ${actionCount} tracked items.`;
  } else if (restoredCount > 0) {
    detail = `That undo restored ${restoredCount} tracked item${restoredCount === 1 ? "" : "s"}.`;
  }
  return { reference, detail };
};

const statusLabelFor = (action, actionMap) => {
  const revertedById = String(action?.reverted_by_action_id || "").trim();
  if (action?.reverted_at || revertedById) {
    const revertAction = actionMap.get(revertedById);
    const actionCount = itemCountFor(action);
    const restoredCount = itemCountFor(revertAction);
    if (actionCount > 0 && restoredCount > 0 && restoredCount < actionCount) {
      return "partly undone";
    }
    return "reverted";
  }
  const kind = String(action?.kind || "").trim().toLowerCase();
  const raw = String(action?.status || "").trim().toLowerCase();
  if (kind === "revert" && (!raw || raw === "applied")) return "applied";
  if (!raw) return "saved";
  if (raw === "applied") return "saved";
  return raw;
};

const buildFallbackDiff = (item) => {
  const beforeText = item?.diff?.before_text || "";
  const afterText = item?.diff?.after_text || "";
  const parts = [];
  if (beforeText) parts.push(`--- before\n${beforeText}`);
  if (afterText) parts.push(`+++ after\n${afterText}`);
  return parts.join("\n\n") || "No textual diff available.";
};

const normalizeDocsFocusTarget = (value) => {
  if (value == null) return "";
  return String(value).replace(/\\/g, "/").trim();
};

const buildDocsHref = (item) => {
  if (!item || typeof item !== "object") return "";
  const section = String(item.section || "").trim().toLowerCase();
  const resourceType = String(item.resource_type || "").trim().toLowerCase();
  if (resourceType !== "file" && section !== "knowledge") return "";
  const focusTarget = normalizeDocsFocusTarget(
    item.resource_id || item.label || item.resource_key,
  );
  if (!focusTarget) return "";
  return `/knowledge?tab=documents&id=${encodeURIComponent(focusTarget)}`;
};

const groupActions = (actions) => {
  const conversations = new Map();
  (Array.isArray(actions) ? actions : []).forEach((action) => {
    if (!action || typeof action !== "object" || !action.id) return;
    const conversationKey = action.conversation_id || "__workspace__";
    if (!conversations.has(conversationKey)) {
      conversations.set(conversationKey, {
        key: conversationKey,
        conversationId: action.conversation_id || null,
        label: conversationLabelFor(action),
        latestTs: Number(action.created_at_ts || action.timestamp || 0),
        responses: new Map(),
      });
    }
    const conversation = conversations.get(conversationKey);
    conversation.latestTs = Math.max(
      conversation.latestTs || 0,
      Number(action.created_at_ts || action.timestamp || 0),
    );
    const responseKey = action.response_id || "__outside_chat__";
    if (!conversation.responses.has(responseKey)) {
      conversation.responses.set(responseKey, {
        key: responseKey,
        responseId: action.response_id || null,
        label: responseLabelFor(action),
        latestTs: Number(action.created_at_ts || action.timestamp || 0),
        actions: [],
      });
    }
    const response = conversation.responses.get(responseKey);
    response.latestTs = Math.max(
      response.latestTs || 0,
      Number(action.created_at_ts || action.timestamp || 0),
    );
    response.actions.push(action);
  });
  return [...conversations.values()]
    .map((conversation) => ({
      ...conversation,
      responses: [...conversation.responses.values()]
        .map((response) => ({
          ...response,
          actions: [...response.actions].sort(
            (a, b) =>
              (Number(b?.created_at_ts || b?.timestamp) || 0) -
              (Number(a?.created_at_ts || a?.timestamp) || 0),
          ),
        }))
        .sort((a, b) => (b.latestTs || 0) - (a.latestTs || 0)),
    }))
    .sort((a, b) => (b.latestTs || 0) - (a.latestTs || 0));
};

const ActionHistoryPanel = ({ actions = [], backendReady = true, onRefresh }) => {
  const navigate = useNavigate();
  const [details, setDetails] = React.useState({});
  const [pendingKey, setPendingKey] = React.useState("");
  const [feedback, setFeedback] = React.useState("");
  const groups = React.useMemo(() => groupActions(actions), [actions]);
  const actionMap = React.useMemo(() => {
    const next = new Map();
    (Array.isArray(actions) ? actions : []).forEach((action) => {
      if (!action?.id) return;
      next.set(String(action.id), action);
    });
    return next;
  }, [actions]);

  const toggleDiff = async (actionId) => {
    const current = details[actionId];
    if (current?.open) {
      setDetails((prev) => ({
        ...prev,
        [actionId]: { ...prev[actionId], open: false },
      }));
      return;
    }
    if (current?.action) {
      setDetails((prev) => ({
        ...prev,
        [actionId]: { ...prev[actionId], open: true },
      }));
      return;
    }
    setDetails((prev) => ({
      ...prev,
      [actionId]: { ...(prev[actionId] || {}), loading: true, error: "", open: true },
    }));
    try {
      const res = await axios.get(`/api/actions/${encodeURIComponent(actionId)}`);
      setDetails((prev) => ({
        ...prev,
        [actionId]: {
          loading: false,
          error: "",
          open: true,
          action: res?.data?.action || null,
        },
      }));
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err?.message || "Failed to load action diff.";
      setDetails((prev) => ({
        ...prev,
        [actionId]: { loading: false, error: String(detail), open: true, action: null },
      }));
    }
  };

  const runRevert = async (key, payload, successMessage) => {
    if (!backendReady) return;
    setPendingKey(key);
    setFeedback("");
    try {
      const res = await axios.post("/api/actions/revert", payload);
      const actionSummary = res?.data?.action?.summary;
      setFeedback(actionSummary || successMessage);
      onRefresh?.();
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to revert action.";
      setFeedback(String(detail));
    } finally {
      setPendingKey("");
    }
  };

  if (!groups.length && !feedback) {
    return null;
  }

  return (
    <section className="action-history-panel" aria-label="write history">
      <div className="action-history-header">
        <div>
          <h3>write history</h3>
          <p className="status-note">
            Revert tracked writes individually, by response, or by conversation.
          </p>
        </div>
      </div>
      {feedback && <p className="agent-console-note">{feedback}</p>}
      <div className="action-history-groups">
        {groups.map((conversation) => (
          <article key={conversation.key} className="action-conversation-group">
            <div className="action-group-header">
              <div>
                <h4>{conversation.label}</h4>
                <span className="action-group-meta">
                  {conversation.responses.reduce(
                    (count, response) => count + response.actions.length,
                    0,
                  )}{" "}
                  tracked writes
                </span>
              </div>
              {conversation.conversationId && (
                <button
                  type="button"
                  className="agent-card-control-btn"
                  disabled={pendingKey === `conversation:${conversation.conversationId}`}
                  onClick={() =>
                    runRevert(
                      `conversation:${conversation.conversationId}`,
                      { conversation_id: conversation.conversationId, force: false },
                      `Reverted conversation ${conversation.label}.`,
                    )
                  }
                >
                  Revert conversation
                </button>
              )}
            </div>
            {conversation.responses.map((response) => (
              <div key={response.key} className="action-response-group">
                <div className="action-response-header">
                  <div>
                    <strong>{response.label}</strong>
                    <span className="action-group-meta">
                      {response.actions.length} action{response.actions.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  {response.responseId && (
                    <button
                      type="button"
                      className="agent-card-control-btn"
                      disabled={pendingKey === `response:${response.responseId}`}
                      onClick={() =>
                        runRevert(
                          `response:${response.responseId}`,
                          {
                            response_id: response.responseId,
                            conversation_id: conversation.conversationId,
                            force: false,
                          },
                          `Reverted ${response.label}.`,
                        )
                      }
                    >
                      Revert response
                    </button>
                  )}
                </div>
                <ul className="action-list">
                  {response.actions.map((action) => {
                    const isExpanded = !!details[action.id]?.open;
                    const detail = details[action.id];
                    const statusLabel = statusLabelFor(action, actionMap);
                    const kindLabel = actionKindLabelFor(action);
                    const nameLabel = actionNameLabelFor(action);
                    const showName = !!nameLabel && nameLabel !== kindLabel;
                    const itemCount = itemCountFor(action);
                    const relation = describeActionReference(action, actionMap);
                    const canRevert = !!action.revertible;
                    return (
                      <li key={action.id} className="action-row">
                        <div className="action-row-top">
                          <div className="action-row-copy">
                            <div className="action-row-meta">
                              <span className="agent-activity-type">{kindLabel}</span>
                              {showName && <span className="agent-activity-name">{nameLabel}</span>}
                              <span className="agent-activity-status">{statusLabel}</span>
                              {itemCount > 0 && (
                                <span className="action-item-count">
                                  {itemCount} item{itemCount === 1 ? "" : "s"}
                                </span>
                              )}
                              {formatTimestamp(action.created_at_ts || action.timestamp) && (
                                <time>{formatTimestamp(action.created_at_ts || action.timestamp)}</time>
                              )}
                            </div>
                            <p className="action-row-summary">{actionTitleFor(action)}</p>
                            {relation?.reference && (
                              <p className="action-row-reference">{relation.reference}</p>
                            )}
                            {relation?.detail && <p className="action-row-note">{relation.detail}</p>}
                          </div>
                          <div className="action-row-actions">
                            <button
                              type="button"
                              className="agent-card-control-btn"
                              onClick={() => toggleDiff(action.id)}
                              disabled={!backendReady}
                            >
                              {isExpanded ? "Hide diff" : "Show diff"}
                            </button>
                            <button
                              type="button"
                              className="agent-card-control-btn"
                              disabled={!canRevert || pendingKey === `action:${action.id}`}
                              onClick={() =>
                                runRevert(
                                  `action:${action.id}`,
                                  { action_ids: [action.id], force: false },
                                  `Reverted ${action.summary || action.name || "action"}.`,
                                )
                              }
                            >
                              Revert action
                            </button>
                          </div>
                        </div>
                        {isExpanded && (
                          <div className="action-diff-panel">
                            {detail?.loading && <p className="status-note">Loading diff…</p>}
                            {detail?.error && <p className="status-note">{detail.error}</p>}
                            {detail?.action?.items?.length ? (
                              detail.action.items.map((item) => (
                                <div
                                  key={`${action.id}:${item.id || item.resource_key}`}
                                  className="action-diff-item"
                                >
                                  <div className="action-diff-meta">
                                    <strong>{item.label || item.resource_id}</strong>
                                    <div className="action-row-actions">
                                      <span className="agent-activity-status">
                                        {item.operation || "update"}
                                      </span>
                                      <span className="action-item-count">
                                        {item.section || item.resource_type}
                                      </span>
                                      {buildDocsHref(item) && (
                                        <button
                                          type="button"
                                          className="agent-card-control-btn"
                                          onClick={() => navigate(buildDocsHref(item))}
                                        >
                                          Open in docs
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                  <pre>{item?.diff?.unified || buildFallbackDiff(item)}</pre>
                                </div>
                              ))
                            ) : !detail?.loading && !detail?.error ? (
                              <p className="status-note">No diff details available.</p>
                            ) : null}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </article>
        ))}
      </div>
    </section>
  );
};

export default ActionHistoryPanel;
