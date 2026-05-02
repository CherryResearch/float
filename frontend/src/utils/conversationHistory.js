import {
  isContinuationPlaceholderText,
  stripInlineToolPlaceholders,
} from "./continuationText";

export const hasInlineToolPlaceholder = (value) =>
  /\[\[tool_call:\d+\]\]/.test(String(value || ""));

export const hasRenderableAssistantContent = (value) =>
  Boolean(stripInlineToolPlaceholders(value).trim());

export const normalizeConversationHistoryRole = (role) => {
  const normalized = String(role || "").trim().toLowerCase();
  if (normalized === "assistant" || normalized === "ai") return "ai";
  if (normalized === "user") return "user";
  return null;
};

export const buildHistoryFromConversation = (conversation) =>
  (Array.isArray(conversation) ? conversation : []).reduce((history, entry) => {
    if (!entry || typeof entry !== "object") return history;
    const role = normalizeConversationHistoryRole(entry.role);
    if (!role) return history;
    const text = typeof entry.text === "string" ? entry.text : String(entry.text || "");
    const trimmed = text.trim();
    if (!trimmed) return history;
    const metadata =
      entry.metadata && typeof entry.metadata === "object" ? entry.metadata : {};
    const explicitToolPlaceholder =
      role === "ai" &&
      isContinuationPlaceholderText(trimmed) &&
      (metadata.tool_response_pending || Array.isArray(entry.tools));
    const pendingInlinePlaceholder =
      role === "ai" &&
      metadata.tool_response_pending &&
      hasInlineToolPlaceholder(trimmed) &&
      !hasRenderableAssistantContent(trimmed);
    if (explicitToolPlaceholder || pendingInlinePlaceholder) return history;
    history.push({ role, text });
    return history;
  }, []);
