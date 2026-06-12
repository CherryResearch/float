export const TOOL_REVIEW_ACTION_EVENT = "float:tool-review-action";

const VALID_TOOL_REVIEW_ACTIONS = new Set(["accept", "deny", "edit", "continue"]);

export const normalizeToolReviewAction = (value) => {
  const action = String(value || "").trim().toLowerCase();
  return VALID_TOOL_REVIEW_ACTIONS.has(action) ? action : "";
};

export const normalizeToolReviewStringArray = (value) => {
  const source = Array.isArray(value) ? value : [value];
  const seen = new Set();
  const result = [];
  source.forEach((item) => {
    const str = String(item ?? "").trim();
    if (!str || seen.has(str)) return;
    seen.add(str);
    result.push(str);
  });
  return result;
};

export const normalizeToolReviewTarget = (value = {}) => {
  const source = value && typeof value === "object" ? value : {};
  const data =
    source.data && typeof source.data === "object" ? source.data : source;
  const selectedToolId = String(
    data.selectedToolId ?? data.selected_tool_id ?? data.toolId ?? data.tool_id ?? "",
  ).trim();
  const toolIds = normalizeToolReviewStringArray(
    data.toolIds ??
      data.tool_ids ??
      data.toolId ??
      data.tool_id ??
      data.request_id,
  );
  const toolNames = normalizeToolReviewStringArray(
    data.toolNames ?? data.tool_names ?? data.toolName ?? data.tool_name,
  );
  const messageId = String(data.messageId ?? data.message_id ?? "").trim();
  const chainId = String(data.chainId ?? data.chain_id ?? messageId ?? "").trim();
  const sessionId = String(data.sessionId ?? data.session_id ?? "").trim();
  const agentId = String(data.agentId ?? data.agent_id ?? "").trim();
  return {
    toolIds,
    toolId: toolIds[0] || "",
    selectedToolId: selectedToolId || toolIds[0] || "",
    toolNames,
    toolName: toolNames[0] || "",
    messageId,
    chainId,
    sessionId,
    agentId,
    batchId: String(data.batchId ?? data.batch_id ?? "").trim(),
    scope: String(data.scope ?? "").trim().toLowerCase() === "batch" ? "batch" : "selected",
    actionUrl: String(data.actionUrl ?? data.action_url ?? "").trim(),
    notificationId: String(data.notificationId ?? data.notification_id ?? "").trim(),
  };
};

export const toolReviewItems = (target) => {
  const source =
    target && typeof target === "object" && target.data && typeof target.data === "object"
      ? target.data
      : target || {};
  const normalized = normalizeToolReviewTarget(target);
  const toolArgs = Array.isArray(source.toolArgs)
    ? source.toolArgs
    : Array.isArray(source.tool_args)
      ? source.tool_args
      : [];
  const toolStatuses = Array.isArray(source.toolStatuses)
    ? source.toolStatuses
    : Array.isArray(source.tool_statuses)
      ? source.tool_statuses
      : [];
  const maxLength = Math.max(normalized.toolIds.length, normalized.toolNames.length, 1);
  const items = [];
  for (let index = 0; index < maxLength; index += 1) {
    const toolId = normalized.toolIds[index] || "";
    const toolName = normalized.toolNames[index] || "";
    if (!toolId && !toolName) continue;
    items.push({
      id: toolId || `${normalized.batchId || normalized.notificationId || "tool"}:${index}`,
      toolId,
      toolName,
      args: toolArgs[index],
      status: String(toolStatuses[index] || "").trim().toLowerCase(),
      index,
      label: toolName || (toolId ? `tool ${toolId.slice(0, 8)}` : `tool ${index + 1}`),
    });
  }
  return items;
};

export const escapeToolReviewSelectorValue = (value) => {
  const str = String(value ?? "");
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(str);
  }
  return str.replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
};

export const toolReviewScopeSelectors = (target) => {
  const normalized = normalizeToolReviewTarget(target);
  const selectors = [];
  const scopedToolIds = normalized.scope !== "batch" && normalized.selectedToolId
    ? [normalized.selectedToolId]
    : normalized.toolIds;
  scopedToolIds.forEach((id) => {
    selectors.push(`[data-tool-id="${escapeToolReviewSelectorValue(id)}"]`);
  });
  [normalized.chainId, normalized.messageId]
    .filter(Boolean)
    .forEach((id) => {
      selectors.push(`[data-chain-id="${escapeToolReviewSelectorValue(id)}"]`);
    });
  if (normalized.agentId) {
    selectors.push(`[data-agent-id="${escapeToolReviewSelectorValue(normalized.agentId)}"]`);
  }
  return normalizeToolReviewStringArray(selectors);
};

export const dispatchToolReviewAction = (action, target = {}) => {
  const normalizedAction = normalizeToolReviewAction(action);
  if (!normalizedAction || typeof window === "undefined") return null;
  const detail = {
    ...normalizeToolReviewTarget(target),
    action: normalizedAction,
    handled: false,
  };
  const event = new CustomEvent(TOOL_REVIEW_ACTION_EVENT, { detail });
  window.dispatchEvent(event);
  return event.detail;
};
