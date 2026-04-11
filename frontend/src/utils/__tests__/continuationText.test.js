import { describe, expect, it } from "vitest";

import {
  isContinuationPlaceholderText,
  mergeContinuationText,
  stripInlineToolPlaceholders,
} from "../continuationText";

describe("continuationText", () => {
  it("treats standalone inline tool placeholders as replaceable continuation stubs", () => {
    expect(isContinuationPlaceholderText("[[tool_call:0]]")).toBe(true);
  });

  it("keeps completed assistant text with inline tool links renderable", () => {
    const text = "Checking docs first.[[tool_call:0]]Done.";
    expect(stripInlineToolPlaceholders(text)).toBe("Checking docs first. Done.");
    expect(isContinuationPlaceholderText(text)).toBe(false);
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
