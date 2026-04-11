import { describe, expect, it } from "vitest";

import {
  isKnownDownloadableModel,
  resolveLocalCatalogModelId,
  resolveModelForMode,
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
  });

  it("treats provider-first e4b as downloadable", () => {
    expect(isKnownDownloadableModel("gemma-4-E4B-it")).toBe(true);
  });
});
