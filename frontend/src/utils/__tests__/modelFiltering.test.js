import { describe, expect, it } from "vitest";

import {
  filterAvailableModelsForField,
  isLikelyEmbeddingModelName,
} from "../modelFiltering";

describe("modelFiltering", () => {
  it("detects embedding model names", () => {
    expect(isLikelyEmbeddingModelName("all-MiniLM-L6-v2")).toBe(true);
  });

  it("filters model lists per settings field by default", () => {
    const models = [
      "gpt-oss-20b",
      "all-MiniLM-L6-v2",
      "whisper-small",
      "clip-vit-base-patch32",
      "kokoro",
      "voxtral-mini-3b-2507",
    ];

    expect(filterAvailableModelsForField("transformer_model", models)).toEqual([
      "gpt-oss-20b",
    ]);
    expect(filterAvailableModelsForField("stt_model", models)).toEqual([
      "whisper-small",
    ]);
    expect(filterAvailableModelsForField("vision_model", models)).toEqual([
      "clip-vit-base-patch32",
    ]);
    expect(filterAvailableModelsForField("tts_model", models)).toEqual(["kokoro"]);
    expect(filterAvailableModelsForField("voice_model", models)).toEqual([
      "voxtral-mini-3b-2507",
    ]);
  });

  it("allows opt-in unfiltered lists", () => {
    const models = [
      "gpt-oss-20b",
      "all-MiniLM-L6-v2",
      "whisper-small",
      "clip-vit-base-patch32",
    ];
    expect(
      filterAvailableModelsForField("transformer_model", models, {
        includeAll: true,
      }),
    ).toEqual(models);
  });
});

