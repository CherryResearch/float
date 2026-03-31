import { describe, expect, it } from "vitest";

import {
  isContinuationPlaceholderText,
  mergeContinuationText,
} from "../continuationText";

describe("continuationText", () => {
  it("treats inline tool placeholders as replaceable continuation stubs", () => {
    expect(isContinuationPlaceholderText("Checking docs first.[[tool_call:0]]")).toBe(true);
  });

  it("replaces pending tool stub text instead of appending the continuation", () => {
    expect(
      mergeContinuationText("Checking docs first.[[tool_call:0]]", "Use computer.session.start first.", {
        tool_response_pending: true,
      }),
    ).toBe("Use computer.session.start first.");
  });

  it("does not append exact duplicate continuation text", () => {
    expect(mergeContinuationText("I will use the computer tools.", "I will use the computer tools.")).toBe(
      "I will use the computer tools.",
    );
  });
});
