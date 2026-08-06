export const hasUnacknowledgedClientOutboxTurn = (messages) => {
  const transcript = Array.isArray(messages) ? messages : [];
  const assistant = transcript[transcript.length - 1];
  if (!assistant || typeof assistant !== "object") return false;
  const role = String(assistant.role || "").trim().toLowerCase();
  if (role !== "ai" && role !== "assistant") return false;
  const user = transcript[transcript.length - 2];
  return Boolean(
    assistant.metadata?.client_outbox ||
      (user && typeof user === "object" && user.metadata?.client_outbox),
  );
};

export const clearMissingConversationHydrationState = (state, sessionId) => {
  if (!state || String(state.sessionId || "").trim() !== String(sessionId || "").trim()) {
    return state;
  }
  return {
    ...state,
    conversation: [],
    conversationTrimMeta: null,
    history: [],
  };
};

const messageId = (message) => String(message?.id || "").trim();

const messageSignature = (message) => {
  try {
    return JSON.stringify(message);
  } catch {
    return String(message);
  }
};

export const mergeCanonicalConversationWithLocalChanges = (
  canonicalMessages,
  hydrationBaseline,
  currentMessages,
) => {
  const canonical = Array.isArray(canonicalMessages) ? canonicalMessages : [];
  const baseline = Array.isArray(hydrationBaseline) ? hydrationBaseline : [];
  const current = Array.isArray(currentMessages) ? currentMessages : [];
  const baselineIds = new Set(
    baseline.map((message) => messageId(message)).filter(Boolean),
  );
  const canonicalIds = new Set(
    canonical.map((message) => messageId(message)).filter(Boolean),
  );
  const canonicalNoIdSignatures = new Set(
    canonical
      .filter((message) => !messageId(message))
      .map((message) => messageSignature(message)),
  );
  const changed = current.filter((message, index) => {
    const id = messageId(message);
    if (id) {
      return !baselineIds.has(id) && !canonicalIds.has(id);
    }
    return (
      index >= baseline.length &&
      !canonicalNoIdSignatures.has(messageSignature(message))
    );
  });
  if (!changed.length) return canonical;

  return [...canonical, ...changed];
};

export const shouldResumeMissingConversationAutosave = ({
  hydration,
  sessionId,
  messages,
}) =>
  Boolean(
    hydration?.status === "missing" &&
      hydration.sessionId === String(sessionId || "").trim() &&
      Array.isArray(messages) &&
      messages.length > 0 &&
      !hasUnacknowledgedClientOutboxTurn(messages),
  );

const normalizeSessionId = (sessionId) => String(sessionId || "").trim();

const CONVERSATION_HYDRATION_RETRY_DELAYS_MS = [250, 750, 2000];

export const getConversationHydrationRetryDelay = (failureIndex) => {
  const index = Number(failureIndex);
  if (!Number.isInteger(index) || index < 0) return null;
  return CONVERSATION_HYDRATION_RETRY_DELAYS_MS[index] ?? null;
};

export const createConversationHydrationGate = (sessionId) => {
  let state = {
    sessionId: normalizeSessionId(sessionId),
    status: normalizeSessionId(sessionId) ? "pending" : "ready",
    requestId: 0,
  };

  const matches = (request) =>
    Boolean(
      request &&
        request.sessionId === state.sessionId &&
        request.requestId === state.requestId,
    );

  return {
    begin(nextSessionId) {
      state = {
        sessionId: normalizeSessionId(nextSessionId),
        status: "loading",
        requestId: state.requestId + 1,
      };
      return { ...state };
    },
    acknowledge(request) {
      if (!matches(request)) return false;
      state = { ...state, status: "ready" };
      return true;
    },
    fail(request) {
      if (!matches(request)) return false;
      state = { ...state, status: "failed" };
      return true;
    },
    markMissing(request) {
      if (!matches(request)) return false;
      state = { ...state, status: "missing" };
      return true;
    },
    bypass(nextSessionId) {
      state = {
        sessionId: normalizeSessionId(nextSessionId),
        status: "ready",
        requestId: state.requestId + 1,
      };
    },
    canPersist(nextSessionId) {
      return (
        state.status === "ready" &&
        state.sessionId === normalizeSessionId(nextSessionId)
      );
    },
    snapshot() {
      return { ...state };
    },
  };
};
