import { describe, expect, it } from "vitest";

import {
  normalizeToolReviewTarget,
  toolReviewScopeSelectors,
} from "../toolReviewActions";

describe("toolReviewActions", () => {
  it("does not infer a selected tool from a batch of tool ids", () => {
    expect(
      normalizeToolReviewTarget({
        scope: "selected",
        toolIds: ["tool-first", "tool-second"],
      }),
    ).toEqual(
      expect.objectContaining({
        selectedToolId: "",
        toolId: "tool-first",
        toolIds: ["tool-first", "tool-second"],
      }),
    );
  });

  it("uses only the explicit tool id selector for selected actions", () => {
    expect(
      toolReviewScopeSelectors({
        scope: "selected",
        selectedToolId: "tool-second",
        toolIds: ["tool-first", "tool-second"],
        chainId: "message-1",
        agentId: "agent-1",
      }),
    ).toEqual(['[data-tool-id="tool-second"]']);

    expect(
      toolReviewScopeSelectors({
        scope: "selected",
        toolIds: ["tool-first", "tool-second"],
        chainId: "message-1",
      }),
    ).toEqual([]);
  });

  it("keeps explicit batch selectors for all-tool actions", () => {
    expect(
      toolReviewScopeSelectors({
        scope: "batch",
        toolIds: ["tool-first", "tool-second"],
        chainId: "message-1",
        agentId: "agent-1",
      }),
    ).toEqual([
      '[data-tool-id="tool-first"]',
      '[data-tool-id="tool-second"]',
      '[data-chain-id="message-1"]',
      '[data-agent-id="agent-1"]',
    ]);
  });
});
