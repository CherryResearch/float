// A lost HTTP response must not cause the same turn or its tools to run again.
// Read the already-reserved message while the original request is still pending.
export const waitForChatResponse = (
  request,
  { readConversation, messageId, signal, pollMs = 30000, maxWaitMs = 30 * 60 * 1000 },
) => new Promise((resolve, reject) => {
  let settled = false;
  let pollTimer;
  let deadlineTimer;
  const finish = (callback, value) => {
    if (settled) return;
    settled = true;
    clearTimeout(pollTimer);
    clearTimeout(deadlineTimer);
    signal?.removeEventListener("abort", onAbort);
    callback(value);
  };
  const onAbort = () => {
    const error = new Error("Generation cancelled");
    error.code = "ERR_CANCELED";
    finish(reject, error);
  };
  const checkSavedMessage = async () => {
    try {
      const data = await readConversation();
      if (settled) return;
      const message = data?.messages?.find((entry) => entry?.id === messageId);
      const metadata = message?.metadata || {};
      const status = String(metadata.status || "").toLowerCase();
      const finished = Boolean(metadata.timing?.completed_at) &&
        ["complete", "completed", "cancelled", "canceled", "error", "timeout", "partial"].includes(status);
      const awaitingTools = metadata.tool_response_pending &&
        message?.tools?.some((tool) => tool?.id || tool?.request_id);
      if (finished || awaitingTools) {
        finish(resolve, {
          message: message.text || "",
          thought: Array.isArray(message.thoughts) ? message.thoughts.join("") : "",
          tools_used: message.tools || [],
          metadata: { ...metadata, recovered_from_conversation: true },
        });
        return;
      }
    } catch {
      // A temporary read failure is not evidence that the original request failed.
    }
    if (!settled) pollTimer = setTimeout(checkSavedMessage, pollMs);
  };
  Promise.resolve(request).then(
    (value) => finish(resolve, value),
    (error) => finish(reject, error),
  );
  signal?.addEventListener("abort", onAbort, { once: true });
  if (signal?.aborted) {
    onAbort();
    return;
  }
  pollTimer = setTimeout(checkSavedMessage, pollMs);
  deadlineTimer = setTimeout(() => {
    const error = new Error("Float stopped waiting for the response. The request may still be running; check the saved conversation before sending it again.");
    error.code = "REQUEST_OUTCOME_UNKNOWN";
    // This only releases the UI wait. It does not abort or replay provider work.
    finish(reject, error);
  }, maxWaitMs);
});
