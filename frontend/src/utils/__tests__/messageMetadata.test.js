import { describe, expect, it } from "vitest";

import {
  getMessageModelLabel,
  getMessageTimingRows,
  getMessageUsageLabel,
  getProviderReasoningTokens,
} from "../messageMetadata";

describe("message metadata", () => {
  it("keeps the requested alias alongside the model reported for this response", () => {
    expect(getMessageModelLabel({
      model: "chat-latest",
      model_requested: "chat-latest",
      model_received: "gpt-6.1-sol",
    })).toBe("GPT latest (gpt-6.1-sol)");
  });

  it("does not invent a concrete model when the provider echoes the alias", () => {
    expect(getMessageModelLabel({
      model_requested: "chat-latest",
      model_received: "chat-latest",
    })).toBe("GPT latest (model not reported)");
    expect(getMessageModelLabel({ model: "chat-latest" })).toBe("GPT latest (model not reported)");
  });

  it("uses recorded per-turn resolution when the response echoes the alias", () => {
    expect(getMessageModelLabel({
      model_requested: "chat-latest",
      model_received: "chat-latest",
      model_resolved: "gpt-6.1-sol",
    })).toBe("GPT latest (gpt-6.1-sol)");
  });

  it("prefers the provider's model over stale requested-model metadata", () => {
    expect(getMessageModelLabel({
      model: "gpt-6.1-sol",
      model_requested: "gpt-6.1-sol",
      model_received: "gpt-6.1-sol-2026-10-01",
    })).toBe("gpt-6.1-sol-2026-10-01");
  });

  it("shows explicit lifecycle times and elapsed time across tool waiting", () => {
    const rows = getMessageTimingRows({
      timestamp: "2026-10-29T18:00:00Z",
      metadata: {
        timing: {
          sent_at: "2026-10-29T18:00:00Z",
          accepted_at: 1793296801,
          first_output_at: "2026-10-29T18:00:03Z",
          completed_at: "2026-10-29T18:02:01Z",
        },
        tool_continuation_rounds: 1,
      },
    });
    expect(rows.map((row) => row.label)).toEqual(["Sent", "Accepted", "First output", "Completed", "Elapsed"]);
    expect(rows.at(-1).value).toBe("120.0 s (including tool wait)");
  });

  it("does not present an old placeholder or edit time as received time", () => {
    const rows = getMessageTimingRows({
      timestamp: "2026-10-29T18:00:00Z",
      metadata: { status: "complete", updated_at: 1793296921 },
    });
    expect(rows.map((row) => row.label)).toEqual(["Message time"]);
  });

  it("omits elapsed time for invalid, missing, or reversed lifecycle times", () => {
    expect(getMessageTimingRows({ metadata: { timing: { accepted_at: "invalid" } } })).toEqual([]);
    expect(getMessageTimingRows({ metadata: { timing: { accepted_at: [0], completed_at: false } } })).toEqual([]);
    expect(getMessageTimingRows({ metadata: { timing: { completed_at: "2026-10-29T18:00:00Z" } } }).map((row) => row.label)).toEqual(["Completed"]);
    expect(getMessageTimingRows({ metadata: { timing: { accepted_at: 200, completed_at: 100 } } }).some((row) => row.label === "Elapsed")).toBe(false);
  });

  it("distinguishes estimates from counts partly completed with estimates", () => {
    expect(getMessageUsageLabel({ source: "estimate" })).toBe("Usage (estimated)");
    expect(getMessageUsageLabel({ source: "mixed" })).toBe("Usage (partly estimated)");
    expect(getMessageUsageLabel({ source: "provider" })).toBe("Usage");
  });

  it("reports reasoning tokens only when the provider supplies a valid count", () => {
    expect(getProviderReasoningTokens({ source: "provider", output_tokens_details: { reasoning_tokens: 512 } })).toBe(512);
    expect(getProviderReasoningTokens({ completion_tokens_details: { reasoning_tokens: 0 } })).toBe(0);
    expect(getProviderReasoningTokens({ source: "estimate", output_tokens_details: { reasoning_tokens: 512 } })).toBeNull();
    expect(getProviderReasoningTokens({ completion_tokens: 512 })).toBeNull();
    expect(getProviderReasoningTokens({ output_tokens_details: { reasoning_tokens: -1 } })).toBeNull();
  });
});
