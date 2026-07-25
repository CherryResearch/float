import { describe, expect, it } from "vitest";
import {
  formatTokenLimit,
  normalizeCustomOutputTokens,
  normalizeOutputTokenMode,
  outputTokenPayload,
  resolveModelCapabilities,
  selectedOutputTokenLimit,
} from "../generationLimits";

describe("generation limits", () => {
  it("omits an output limit in auto mode", () => {
    expect(normalizeOutputTokenMode("provider")).toBe("auto");
    expect(selectedOutputTokenLimit("auto", 32768)).toBeNull();
    expect(outputTokenPayload("auto", 32768)).toEqual({});
  });

  it("supports large presets and bounded custom values", () => {
    expect(outputTokenPayload("262144", 1)).toEqual({
      max_output_tokens: 262144,
    });
    expect(normalizeCustomOutputTokens("9999999")).toBe(2_000_000);
    expect(outputTokenPayload("custom", "75000")).toEqual({
      max_output_tokens: 75000,
    });
  });

  it("resolves provider-reported model capabilities", () => {
    expect(
      resolveModelCapabilities(
        [
          {
            id: "thinkingmachines/Inkling",
            max_context_length: 65536,
            max_output_tokens: 32768,
            source: "tinker-sdk",
          },
        ],
        "thinkingmachines/Inkling",
      ),
    ).toEqual({
      id: "thinkingmachines/Inkling",
      maxContextLength: 65536,
      maxOutputTokens: 32768,
      source: "tinker-sdk",
      kind: "",
    });
    expect(formatTokenLimit(65536)).toBe("64K");
    expect(formatTokenLimit(1048576)).toBe("1M");
  });
});
