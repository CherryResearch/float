import { describe, expect, it } from "vitest";
import {
  isCustomReasoningEffort,
  normalizeThinkingMode,
  reasoningEffortValue,
  thinkingPayloadForMode,
} from "../reasoningEffort";

describe("reasoning effort", () => {
  it("keeps named presets and legacy booleans compatible", () => {
    expect(normalizeThinkingMode("medium")).toBe("medium");
    expect(normalizeThinkingMode("max")).toBe("xhigh");
    expect(normalizeThinkingMode("true")).toBe("high");
    expect(thinkingPayloadForMode("minimal")).toEqual({ thinking: "minimal" });
  });

  it("sends custom effort as a numeric value", () => {
    expect(normalizeThinkingMode("0.834")).toBe("0.83");
    expect(isCustomReasoningEffort("0.83")).toBe(true);
    expect(reasoningEffortValue("0.83")).toBe(0.83);
    expect(thinkingPayloadForMode("0.83")).toEqual({ thinking: 0.83 });
  });

  it("clamps custom effort to Tinker's supported range", () => {
    expect(thinkingPayloadForMode("1.5")).toEqual({ thinking: 0.99 });
    expect(thinkingPayloadForMode("-1")).toEqual({ thinking: 0 });
    expect(thinkingPayloadForMode("auto")).toEqual({});
  });
});
