import { describe, expect, it } from "vitest";

import {
  API_PROVIDER_MODELS_REFRESH_MS,
  shouldRefreshProviderModels,
} from "../providerProbe";

describe("shouldRefreshProviderModels", () => {
  it("refreshes when provider status is not online", () => {
    expect(
      shouldRefreshProviderModels({
        apiProviderStatus: "unauthorized",
        apiModelsUpdatedAt: Date.now(),
      }),
    ).toBe(true);
  });

  it("refreshes when models have never been fetched", () => {
    expect(
      shouldRefreshProviderModels({
        apiProviderStatus: "online",
        apiModelsUpdatedAt: null,
      }),
    ).toBe(true);
  });

  it("skips refresh while models are still fresh", () => {
    const now = 1_000_000;
    expect(
      shouldRefreshProviderModels(
        {
          apiProviderStatus: "online",
          apiModelsUpdatedAt: now - API_PROVIDER_MODELS_REFRESH_MS + 1,
        },
        now,
      ),
    ).toBe(false);
  });

  it("refreshes once the freshness window expires", () => {
    const now = 1_000_000;
    expect(
      shouldRefreshProviderModels(
        {
          apiProviderStatus: "online",
          apiModelsUpdatedAt: now - API_PROVIDER_MODELS_REFRESH_MS,
        },
        now,
      ),
    ).toBe(true);
  });
});
