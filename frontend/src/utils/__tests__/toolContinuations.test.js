import { describe, expect, it } from "vitest";

import {
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

  it("matches a semantic signature when ids differ", () => {
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
  });
});
