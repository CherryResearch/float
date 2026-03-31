export const CONTINUATION_PLACEHOLDER_PATTERNS = [
  /^Requested\s+tools?\b/i,
  /^Tool results:/i,
  /^Tool results are available\./i,
  /^I couldn't finish the continuation from tool results\./i,
];

const INLINE_TOOL_PLACEHOLDER_RE = /\[\[tool_call:\d+\]\]/;

const normalizeContinuationValue = (value) => String(value || "").replace(/\s+/g, " ").trim();

export const isContinuationPlaceholderText = (value) => {
  const trimmed = String(value || "").trim();
  if (!trimmed) return false;
  if (INLINE_TOOL_PLACEHOLDER_RE.test(trimmed)) return true;
  return CONTINUATION_PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(trimmed));
};

export const mergeContinuationText = (existingText, continuation, metadata = {}) => {
  const current = typeof existingText === "string" ? existingText : String(existingText || "");
  const incoming = typeof continuation === "string" ? continuation.trim() : String(continuation || "").trim();
  if (!incoming) return current;

  const currentTrimmed = current.trim();
  if (!currentTrimmed) return incoming;

  const shouldReplace =
    isContinuationPlaceholderText(currentTrimmed) ||
    Boolean(metadata && typeof metadata === "object" && metadata.tool_response_pending);
  if (shouldReplace) return incoming;

  const normalizedCurrent = normalizeContinuationValue(currentTrimmed);
  const normalizedIncoming = normalizeContinuationValue(incoming);
  if (!normalizedIncoming) return currentTrimmed;
  if (normalizedCurrent === normalizedIncoming) return currentTrimmed;
  if (normalizedCurrent.endsWith(normalizedIncoming)) return currentTrimmed;
  if (normalizedIncoming.startsWith(normalizedCurrent)) return incoming;

  return `${current}\n\n${incoming}`.trim();
};
