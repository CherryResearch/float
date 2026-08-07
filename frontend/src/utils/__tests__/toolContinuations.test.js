import { describe, expect, it } from "vitest";

import {
  buildToolContinuationLockKey,
  buildToolContinuationSignature,
  hasMatchingToolContinuationSignature,
} from "../toolContinuations";

describe("tool continuation signatures", () => {
  const baseTool = {
    name: "remember",
    status: "invoked",
    args: { key: "reddit_video_check", value: "same value" },
    result: { status: "invoked", ok: true, message: null, data: "ok" },
  };

  it("keeps the default signature sensitive to request ids", () => {
    const sigA = buildToolContinuationSignature([{ ...baseTool, id: "tool-a" }]);
    const sigB = buildToolContinuationSignature([{ ...baseTool, id: "tool-b" }]);
    expect(sigA).not.toBe(sigB);
  });

  it("matches only the exact saved request signature by default", () => {
    const metadata = {
      tool_continue_signature: buildToolContinuationSignature([
        { ...baseTool, id: "tool-a" },
      ]),
    };

    expect(
      hasMatchingToolContinuationSignature(metadata, [
        { ...baseTool, id: "tool-a" },
      ]),
    ).toBe(true);
    expect(
      hasMatchingToolContinuationSignature(metadata, [
        { ...baseTool, id: "tool-b" },
      ]),
    ).toBe(false);
  });

  it("keeps semantic signatures diagnostic-only when ids differ", () => {
    const metadata = {
      tool_continue_semantic_signature: buildToolContinuationSignature(
        [{ ...baseTool, id: "tool-a" }],
        { includeIds: false },
      ),
    };

    expect(
      hasMatchingToolContinuationSignature(
        metadata,
        [{ ...baseTool, id: "tool-b" }],
        { includeIds: false },
      ),
    ).toBe(true);
    expect(
      hasMatchingToolContinuationSignature(metadata, [
        { ...baseTool, id: "tool-b" },
      ]),
    ).toBe(false);
  });

  it("keys continuation locks by the exact request ids", () => {
    const shared = {
      sessionId: "session-a",
      messageId: "message-a",
    };
    const first = buildToolContinuationLockKey({
      ...shared,
      tools: [{ ...baseTool, id: "tool-a" }],
    });
    const repeated = buildToolContinuationLockKey({
      ...shared,
      tools: [{ ...baseTool, id: "tool-a" }],
    });
    const distinct = buildToolContinuationLockKey({
      ...shared,
      tools: [{ ...baseTool, id: "tool-b" }],
    });

    expect(repeated).toBe(first);
    expect(distinct).not.toBe(first);
  });

  it("canonicalizes compatibility aliases before signing", () => {
    const legacy = buildToolContinuationSignature([
      { ...baseTool, id: "tool-a", name: "memory.read" },
    ]);
    const canonical = buildToolContinuationSignature([
      { ...baseTool, id: "tool-a", name: "recall" },
    ]);

    expect(legacy).toBe(canonical);
  });

  it("matches the backend canonical signature contract", () => {
    expect(
      buildToolContinuationSignature([
        {
          id: "tool-a",
          name: "memory.read",
          status: "invoked",
          args: { key: "tea" },
          result: { data: "oolong" },
        },
      ]),
    ).toBe("d62bd50f");
  });
});
