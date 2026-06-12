export const CONTINUATION_PLACEHOLDER_PATTERNS = [
  /^Requested\s+tools?\b/i,
  /^Tool results:/i,
  /^Tool results are available\./i,
  /^I couldn't finish the continuation from tool results\./i,
];

const INLINE_TOOL_PLACEHOLDER_RE = /\[\[tool_call:\d+\]\]/g;
const INLINE_TOOL_STUB_RE = /\[\[tool_call:\d+\]\]/;

const normalizeContinuationValue = (value) => String(value || "").replace(/\s+/g, " ").trim();
const MAX_TOOL_CONTINUATION_PHASES = 12;

export const stripInlineToolPlaceholders = (value) =>
  String(value || "")
    .replace(INLINE_TOOL_PLACEHOLDER_RE, " ")
    .replace(/\s+/g, " ")
    .trim();

export const isContinuationPlaceholderText = (value) => {
  const trimmed = String(value || "").trim();
  if (!trimmed) return false;
  const stripped = stripInlineToolPlaceholders(trimmed);
  if (!stripped) return true;
  if (INLINE_TOOL_STUB_RE.test(trimmed) && /^response$/i.test(stripped)) {
    return true;
  }
  return CONTINUATION_PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(stripped));
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

export const normalizeToolContinuationPhases = (metadata = {}) => {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const rawPhases = Array.isArray(source.tool_continuation_phases)
    ? source.tool_continuation_phases
    : [];
  const phases = rawPhases
    .map((phase) => {
      if (typeof phase === "string") {
        const text = phase.trim();
        return text ? { text } : null;
      }
      if (!phase || typeof phase !== "object") return null;
      const text = typeof phase.text === "string" ? phase.text.trim() : "";
      if (!text) return null;
      return {
        ...phase,
        text,
      };
    })
    .filter(Boolean);
  const legacyText =
    typeof source.tool_continuation_text === "string"
      ? source.tool_continuation_text.trim()
      : "";
  if (
    legacyText &&
    !phases.some(
      (phase) =>
        normalizeContinuationValue(phase.text) === normalizeContinuationValue(legacyText),
    )
  ) {
    phases.push({ text: legacyText });
  }
  return phases.slice(-MAX_TOOL_CONTINUATION_PHASES);
};

export const appendToolContinuationPhase = (
  metadata = {},
  existingText = "",
  continuation = "",
  options = {},
) => {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const incoming =
    typeof continuation === "string"
      ? continuation.trim()
      : String(continuation || "").trim();
  if (!incoming) return { ...source };

  const phases = normalizeToolContinuationPhases(source);
  const normalizedIncoming = normalizeContinuationValue(incoming);
  if (
    normalizedIncoming &&
    phases.some((phase) => normalizeContinuationValue(phase.text) === normalizedIncoming)
  ) {
    return { ...source, tool_continuation_phases: phases };
  }

  const existingPrelude =
    typeof source.tool_prelude_text === "string"
      ? source.tool_prelude_text
      : typeof existingText === "string"
        ? existingText
        : String(existingText || "");
  const prelude = isContinuationPlaceholderText(existingPrelude)
    ? ""
    : existingPrelude.trim();
  const nextPhase = {
    text: incoming,
    created_at:
      typeof options.createdAt === "string" && options.createdAt.trim()
        ? options.createdAt.trim()
        : new Date().toISOString(),
  };
  return {
    ...source,
    tool_prelude_text: prelude,
    tool_continuation_phases: [...phases, nextPhase].slice(-MAX_TOOL_CONTINUATION_PHASES),
  };
};
