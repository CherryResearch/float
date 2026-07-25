import { describe, expect, it } from "vitest";

import {
  buildModelGroups,
  compareModelIds,
  formatApiModelLabel,
  isKnownDownloadableModel,
  resolveLocalCatalogModelId,
  resolveApiModelAliasTarget,
  resolveModelForMode,
  resolveRequestModelForMode,
  SUGGESTED_LOCAL_MODELS,
  SUGGESTED_SERVER_MODELS,
} from "../modelUtils";

describe("modelUtils", () => {
  it("prefers the active provider marker over a stale direct-local model", () => {
    expect(
      resolveModelForMode({
        backendMode: "local",
        apiModel: "gpt-5.4",
        transformerModel: "lmstudio",
        localModel: "gemma-4-E4B-it",
      }),
    ).toBe("lmstudio");
  });

  it("normalizes repo-style ids for local catalog routes", () => {
    expect(resolveLocalCatalogModelId("google/gemma-3-270m")).toBe("gemma-3-270m");
    expect(resolveLocalCatalogModelId("openai/gpt-oss-20b")).toBe("gpt-oss-20b");
    expect(resolveLocalCatalogModelId("local:google/embeddinggemma-300M")).toBe(
      "embeddinggemma-300M",
    );
  });

  it("does not fall back to api or local models for server mode", () => {
    const selection = {
      backendMode: "server",
      apiModel: "gpt-5.4",
      transformerModel: "",
      localModel: "gpt-oss-20b",
    };

    expect(resolveModelForMode(selection)).toBe("");
    expect(resolveRequestModelForMode(selection)).toBe("");
  });

  it("keeps provider-first Gemma 4 variants in server suggestions only", () => {
    expect(SUGGESTED_SERVER_MODELS).toEqual(
      expect.arrayContaining([
        "gemma-4-12B-it",
        "gemma-4-12B-it-qat-q4_0-gguf",
        "gemma-4-E4B-it",
        "gemma-4-26B-A4B-it",
        "gemma-4-31B-it",
      ]),
    );
    expect(SUGGESTED_LOCAL_MODELS).toEqual(
      expect.arrayContaining(["gemma-4-12B-it-qat-q4_0", "gemma-4-E2B-it"]),
    );
    expect(SUGGESTED_LOCAL_MODELS).not.toEqual(
      expect.arrayContaining([
        "gemma-3-270m",
        "gemma-3-12b-it",
        "gemma-3-27b-it",
        "gemma-4-12B-it-qat-q4_0-gguf",
        "gemma-4-26B-A4B-it",
        "gemma-4-31B-it",
      ]),
    );
  });

  it("treats provider-first Gemma 4 suggestions as downloadable", () => {
    expect(isKnownDownloadableModel("gemma-4-12B-it-qat-q4_0")).toBe(true);
    expect(isKnownDownloadableModel("gemma-4-12B-it-qat-q4_0-gguf")).toBe(true);
    expect(isKnownDownloadableModel("gemma-4-12B-it")).toBe(true);
    expect(isKnownDownloadableModel("gemma-4-E4B-it")).toBe(true);
  });

  it("treats utility embedding and privacy models as downloadable", () => {
    expect(isKnownDownloadableModel("all-MiniLM-L6-v2")).toBe(true);
    expect(isKnownDownloadableModel("all-mpnet-base-v2")).toBe(true);
    expect(isKnownDownloadableModel("embeddinggemma-300m")).toBe(true);
    expect(isKnownDownloadableModel("privacy-filter")).toBe(true);
  });

  it("sorts GPT API models newest to oldest", () => {
    const models = [
      "deepseek-chat",
      "chat-latest",
      "gpt-4.1-mini",
      "gpt-5.4-mini",
      "gpt-5.5",
      "gpt-5.5-pro",
      "gpt-5.4",
      "gpt-5.4-nano",
    ].sort(compareModelIds);

    expect(models).toEqual([
      "chat-latest",
      "gpt-5.5",
      "gpt-5.5-pro",
      "gpt-5.4",
      "gpt-5.4-mini",
      "gpt-5.4-nano",
      "gpt-4.1-mini",
      "deepseek-chat",
    ]);
  });

  it("labels rolling API model aliases with the best stable concrete GPT model", () => {
    const availableModels = [
      "chat-latest",
      "gpt-5.5-2026-07-01",
      "gpt-5.5-pro",
      "gpt-5.5",
      "gpt-5.4-mini",
    ];

    expect(
      resolveApiModelAliasTarget("chat-latest", { availableModels }),
    ).toBe("gpt-5.5");
    expect(formatApiModelLabel("chat-latest", { availableModels })).toBe(
      "GPT latest (gpt-5.5)",
    );
  });

  it("keeps stable GPT family aliases ahead of dated snapshots", () => {
    const models = [
      "gpt-5.5-2026-07-01",
      "gpt-5.5-pro",
      "gpt-5.5",
      "gpt-5.5-2026-08-01",
    ].sort(compareModelIds);

    expect(models).toEqual([
      "gpt-5.5",
      "gpt-5.5-pro",
      "gpt-5.5-2026-08-01",
      "gpt-5.5-2026-07-01",
    ]);
  });

  it("uses backend alias metadata when available", () => {
    expect(
      formatApiModelLabel("chat-latest", {
        aliases: {
          "chat-latest": {
            label: "GPT latest",
            target_model: "gpt-5.6",
          },
        },
        availableModels: ["gpt-5.5"],
      }),
    ).toBe("GPT latest (gpt-5.6)");
  });

  it("labels a persisted deprecated API model with its replacement", () => {
    expect(
      formatApiModelLabel("gpt-5-chat-latest", {
        catalog: [
          {
            id: "gpt-5-chat-latest",
            status: "deprecated",
            replacement: "gpt-5.5",
            persisted_selected: true,
            available: true,
          },
        ],
      }),
    ).toBe("gpt-5-chat-latest (deprecated; migrate to gpt-5.5)");
  });

  it("labels an unavailable persisted snapshot without hiding it", () => {
    expect(
      formatApiModelLabel("gpt-5.5-2026-04-23", {
        catalog: [
          {
            id: "gpt-5.5-2026-04-23",
            status: "fallback",
            replacement: "chat-latest",
            persisted_selected: true,
            available: false,
          },
        ],
      }),
    ).toBe("gpt-5.5-2026-04-23 (unavailable; try chat-latest)");
  });

  it("uses live API models as the primary model group when available", () => {
    const groups = buildModelGroups({
      defaults: ["gpt-5.4", "gpt-5.4-mini"],
      discovered: ["gpt-4.1-mini", "gpt-5.5", "gpt-5.4"],
      current: "gpt-5.4-mini",
    });

    expect(groups.source).toBe("discovered");
    expect(groups.defaults).toEqual(["gpt-5.5", "gpt-5.4", "gpt-4.1-mini"]);
    expect(groups.extras).toEqual(["gpt-5.4-mini"]);
  });
});
