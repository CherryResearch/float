import { describe, expect, it } from "vitest";

import {
  appendToolContinuationPhase,
  isContinuationPlaceholderText,
  mergeContinuationText,
  normalizeToolContinuationPhases,
  stripInlineToolPlaceholders,
} from "../continuationText";

describe("continuationText", () => {
  it("treats standalone inline tool placeholders as replaceable continuation stubs", () => {
    expect(isContinuationPlaceholderText("[[tool_call:0]]")).toBe(true);
  });

  it("treats generic response text plus a tool placeholder as a replaceable stub", () => {
    expect(isContinuationPlaceholderText("response [[tool_call:0]]")).toBe(true);
    expect(
      mergeContinuationText(
        "response [[tool_call:0]]",
        "The search result says sunset is about 8:55 PM.",
      ),
    ).toBe("The search result says sunset is about 8:55 PM.");
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

  it("tracks continuation phases separately from the pre-tool text", () => {
    const metadata = appendToolContinuationPhase(
      {},
      "I will check recall now.",
      "Recall found a food note.",
      { createdAt: "2026-05-21T00:00:00Z" },
    );

    expect(metadata.tool_prelude_text).toBe("I will check recall now.");
    expect(normalizeToolContinuationPhases(metadata)).toEqual([
      {
        text: "Recall found a food note.",
        created_at: "2026-05-21T00:00:00Z",
      },
    ]);
  });

  it("does not add the same continuation phase twice", () => {
    const first = appendToolContinuationPhase(
      {},
      "Requested tool recall.",
      "Recall found a food note.",
      { createdAt: "2026-05-21T00:00:00Z" },
    );
    const second = appendToolContinuationPhase(
      first,
      "Recall found a food note.",
      "Recall found a food note.",
      { createdAt: "2026-05-21T00:00:01Z" },
    );

    expect(second.tool_prelude_text).toBe("");
    expect(normalizeToolContinuationPhases(second)).toHaveLength(1);
  });
});
