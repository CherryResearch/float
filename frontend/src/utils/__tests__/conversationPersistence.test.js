import { describe, expect, it } from "vitest";

import {
  clearMissingConversationHydrationState,
  createConversationHydrationGate,
  getConversationHydrationRetryDelay,
  hasUnacknowledgedClientOutboxTurn,
  mergeCanonicalConversationWithLocalChanges,
  shouldResumeMissingConversationAutosave,
} from "../conversationPersistence";

describe("conversation persistence", () => {
  it("keeps optimistic pending pairs out of authoritative autosaves", () => {
    expect(
      hasUnacknowledgedClientOutboxTurn([
        {
          id: "m1:user",
          role: "user",
          text: "steer this",
          metadata: { client_outbox: true },
        },
        {
          id: "m1",
          role: "ai",
          text: "",
          metadata: { status: "pending", client_outbox: true },
        },
      ]),
    ).toBe(true);
  });

  it("allows completed and failed turns to persist", () => {
    expect(
      hasUnacknowledgedClientOutboxTurn([
        { id: "m1", role: "assistant", text: "done", metadata: { status: "complete" } },
        { id: "m2", role: "ai", text: "failed", metadata: { status: "error" } },
      ]),
    ).toBe(false);
  });

  it("does not let a stale historical pending row block later saves", () => {
    expect(
      hasUnacknowledgedClientOutboxTurn([
        {
          id: "stale",
          role: "ai",
          text: "",
          metadata: { status: "pending", client_outbox: true },
        },
        { id: "m2:user", role: "user", text: "later" },
        { id: "m2", role: "ai", text: "done", metadata: { status: "complete" } },
      ]),
    ).toBe(false);
  });

  it("keeps a client-cancelled request in the outbox until the server acknowledges it", () => {
    expect(
      hasUnacknowledgedClientOutboxTurn([
        { id: "m1:user", role: "user", metadata: { client_outbox: true } },
        {
          id: "m1",
          role: "ai",
          text: "(response stopped)",
          metadata: { status: "cancelled", client_outbox: true },
        },
      ]),
    ).toBe(true);
  });

  it("blocks a stale restored transcript until its server hydration is acknowledged", () => {
    const gate = createConversationHydrationGate("sess-restored");

    expect(gate.canPersist("sess-restored")).toBe(false);
    const request = gate.begin("sess-restored");
    expect(gate.canPersist("sess-restored")).toBe(false);

    expect(gate.acknowledge(request)).toBe(true);
    expect(gate.canPersist("sess-restored")).toBe(true);
  });

  it("does not let a stale hydration response unlock a newer request", () => {
    const gate = createConversationHydrationGate("sess-restored");
    const staleRequest = gate.begin("sess-restored");
    const currentRequest = gate.begin("sess-restored");

    expect(gate.acknowledge(staleRequest)).toBe(false);
    expect(gate.canPersist("sess-restored")).toBe(false);
    expect(gate.acknowledge(currentRequest)).toBe(true);
    expect(gate.canPersist("sess-restored")).toBe(true);
  });

  it("keeps autosave closed after a hydration error but allows an explicit session switch", () => {
    const gate = createConversationHydrationGate("sess-restored");
    const request = gate.begin("sess-restored");

    expect(gate.fail(request)).toBe(true);
    expect(gate.canPersist("sess-restored")).toBe(false);

    gate.bypass("sess-new");
    expect(gate.canPersist("sess-new")).toBe(true);
    expect(gate.canPersist("sess-restored")).toBe(false);
  });

  it("clears a stale local transcript and keeps autosave closed when hydration returns 404", () => {
    const gate = createConversationHydrationGate("sess-deleted");
    const request = gate.begin("sess-deleted");
    const staleState = {
      sessionId: "sess-deleted",
      conversation: [{ id: "failed-tail", role: "ai", text: "stale" }],
      conversationTrimMeta: { truncated: true },
      history: [{ role: "assistant", content: "stale" }],
      composerPreference: "preserved",
    };

    expect(gate.markMissing(request)).toBe(true);
    const cleared = clearMissingConversationHydrationState(
      staleState,
      "sess-deleted",
    );

    expect(cleared).toEqual({
      ...staleState,
      conversation: [],
      conversationTrimMeta: null,
      history: [],
    });
    expect(gate.canPersist("sess-deleted")).toBe(false);
    expect(
      shouldResumeMissingConversationAutosave({
        hydration: gate.snapshot(),
        sessionId: "sess-deleted",
        messages: cleared.conversation,
      }),
    ).toBe(false);
  });

  it("reopens a missing session only after a new turn is acknowledged", () => {
    const gate = createConversationHydrationGate("sess-deleted");
    const request = gate.begin("sess-deleted");
    gate.markMissing(request);
    const pendingMessages = [
      { id: "new:user", role: "user", metadata: { client_outbox: true } },
      { id: "new", role: "ai", metadata: { client_outbox: true } },
    ];
    const acknowledgedMessages = pendingMessages.map((message) => ({
      ...message,
      metadata: {},
    }));

    expect(
      shouldResumeMissingConversationAutosave({
        hydration: gate.snapshot(),
        sessionId: "sess-deleted",
        messages: pendingMessages,
      }),
    ).toBe(false);
    expect(
      shouldResumeMissingConversationAutosave({
        hydration: gate.snapshot(),
        sessionId: "sess-deleted",
        messages: acknowledgedMessages,
      }),
    ).toBe(true);
  });

  it("keeps a turn created while canonical hydration is in flight", () => {
    const restored = [
      { id: "kept", role: "user", text: "canonical question" },
      { id: "stale", role: "ai", text: "failed local tail" },
    ];
    const current = [
      ...restored,
      {
        id: "new:user",
        role: "user",
        text: "sent while loading",
        metadata: { client_outbox: true },
      },
      {
        id: "new",
        role: "ai",
        text: "",
        metadata: { client_outbox: true, status: "pending" },
      },
    ];
    const canonical = [{ id: "kept", role: "user", text: "canonical question" }];

    expect(
      mergeCanonicalConversationWithLocalChanges(canonical, restored, current),
    ).toEqual([canonical[0], current[2], current[3]]);
  });

  it("preserves an acknowledged turn that completed before hydration returned", () => {
    const restored = [{ id: "stale", role: "ai", text: "failed local tail" }];
    const completed = [
      ...restored,
      { id: "new:user", role: "user", text: "retry" },
      { id: "new", role: "ai", text: "completed", metadata: { status: "complete" } },
    ];

    expect(
      mergeCanonicalConversationWithLocalChanges([], restored, completed),
    ).toEqual(completed.slice(1));
  });

  it("does not carry unrelated edits to restored rows over the canonical server copy", () => {
    const restored = [{ id: "same", role: "ai", text: "stale" }];
    const locallyChanged = [{ id: "same", role: "ai", text: "locally changed" }];
    const canonical = [{ id: "same", role: "ai", text: "server" }];

    expect(
      mergeCanonicalConversationWithLocalChanges(
        canonical,
        restored,
        locallyChanged,
      ),
    ).toEqual(canonical);
  });

  it("keeps a canonical final row over a same-id local pending row", () => {
    const restored = [{ id: "answer", role: "ai", text: "old" }];
    const localPending = [
      {
        id: "answer",
        role: "ai",
        text: "",
        metadata: { client_outbox: true, status: "pending" },
      },
    ];
    const canonical = [
      {
        id: "answer",
        role: "ai",
        text: "server final",
        metadata: { status: "complete" },
      },
    ];

    expect(
      mergeCanonicalConversationWithLocalChanges(
        canonical,
        restored,
        localPending,
      ),
    ).toEqual(canonical);
  });

  it("uses bounded backoff for transient hydration failures", () => {
    expect([0, 1, 2, 3].map(getConversationHydrationRetryDelay)).toEqual([
      250,
      750,
      2000,
      null,
    ]);
    expect(getConversationHydrationRetryDelay(-1)).toBeNull();
  });
});
