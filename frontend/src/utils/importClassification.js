const IMPORT_ACTIONS = new Set(["conversation", "document"]);
const IMPORT_CLASSIFICATIONS = new Set(["conversation", "document", "ambiguous"]);

const normalizedString = (value) => String(value ?? "").trim();

const normalizeAction = (value) => {
  const action = normalizedString(value).toLowerCase();
  if (action === "import_conversation" || action === "recognized_messages") {
    return "conversation";
  }
  if (action === "save_document" || action === "knowledge") return "document";
  return IMPORT_ACTIONS.has(action) ? action : "";
};

const normalizeWarnings = (value) => {
  if (Array.isArray(value)) {
    return value.map(normalizedString).filter(Boolean);
  }
  const warning = normalizedString(value);
  return warning ? [warning] : [];
};

export const isMarkdownOrTextImport = (format) =>
  ["md", "markdown", "text", "txt"].includes(normalizedString(format).toLowerCase());

export const normalizeClassifiedImportPreview = (payload, file = null) => {
  const data = payload && typeof payload === "object" ? payload : {};
  const detectedFiles = Array.isArray(data.detected_files) ? data.detected_files : [];
  const firstCandidate =
    detectedFiles.find((item) => item && typeof item === "object") || {};
  // Some backend revisions wrap a single-file classification in detected_files,
  // while others expose it at the top level. Candidate fields take precedence.
  const combined = { ...data, ...firstCandidate };
  const rawClassification = normalizedString(combined.classification).toLowerCase();
  const classification = IMPORT_CLASSIFICATIONS.has(rawClassification)
    ? rawClassification
    : "ambiguous";
  const parsedMessageCount = Number(combined.message_count ?? combined.recognized_message_count ?? 0);
  const messageCount = Number.isFinite(parsedMessageCount)
    ? Math.max(0, Math.trunc(parsedMessageCount))
    : 0;
  const roleCounts =
    combined.role_counts && typeof combined.role_counts === "object" && !Array.isArray(combined.role_counts)
      ? combined.role_counts
      : {};
  let allowedActions = Array.isArray(combined.allowed_actions)
    ? combined.allowed_actions.map(normalizeAction).filter(Boolean)
    : [];
  if (!allowedActions.length) {
    if (classification === "conversation") allowedActions = ["conversation", "document"];
    else if (classification === "ambiguous" && messageCount > 0) {
      allowedActions = ["conversation", "document"];
    } else allowedActions = ["document"];
  }
  allowedActions = [...new Set(allowedActions)];
  const suggestedAction = normalizeAction(combined.suggested_action);
  const warnings = normalizeWarnings(combined.warnings ?? combined.warning);
  if (rawClassification !== classification) {
    warnings.unshift(
      "Float could not confidently classify this file. Saving it as a document is the safe default.",
    );
  }

  return {
    classification,
    messageCount,
    roleCounts,
    preview: normalizedString(combined.preview ?? combined.content_preview ?? combined.excerpt),
    warnings,
    suggestedAction,
    allowedActions,
    sourceName: normalizedString(combined.path ?? combined.name ?? file?.name),
    raw: data,
  };
};

export const describeClassifiedImport = (review) => {
  const classification = normalizedString(review?.classification).toLowerCase();
  const messageCount = Number(review?.messageCount || 0);
  if (classification === "conversation") {
    return {
      title: "Conversation transcript detected",
      detail: `${messageCount} message${messageCount === 1 ? "" : "s"} recognized. You can import the transcript or keep the original file as a document.`,
    };
  }
  if (classification === "ambiguous" && messageCount > 0) {
    return {
      title: "Mixed or ambiguous Markdown",
      detail: `${messageCount} message${messageCount === 1 ? "" : "s"} recognized, but some content may not belong to the transcript. Choose explicitly how to save it.`,
    };
  }
  return {
    title: "Document detected",
    detail: "This does not look like a Float conversation transcript. Save it as a document so it remains available in Documents and knowledge search.",
  };
};
