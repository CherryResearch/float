import { describe, expect, it } from "vitest";

import {
  RUNTIME_AVAILABILITY,
  RUNTIME_PANEL_LANES,
  resolveRuntimePanelContract,
} from "../runtimePanelContract";

describe("resolveRuntimePanelContract", () => {
  it("normalizes a usable Cloud API lane", () => {
    expect(
      resolveRuntimePanelContract({
        mode: "api",
        apiStatus: "online",
        apiProviderStatus: "online",
        apiModel: "gpt-5.4",
      }),
    ).toEqual({
      lane: RUNTIME_PANEL_LANES.CLOUD_API,
      model: "gpt-5.4",
      endpoint: "",
      availability: RUNTIME_AVAILABILITY.USABLE,
      lastOperation: null,
    });
  });

  it.each([
    [{ mode: "api", apiStatus: "loading" }, RUNTIME_AVAILABILITY.CHECKING],
    [{ mode: "api", apiStatus: "degraded" }, RUNTIME_AVAILABILITY.DEGRADED],
    [
      { mode: "api", apiStatus: "online", apiProviderStatus: "unauthorized" },
      RUNTIME_AVAILABILITY.UNAVAILABLE,
    ],
  ])("maps Cloud API status to the shared vocabulary", (input, availability) => {
    expect(resolveRuntimePanelContract(input).availability).toBe(availability);
  });

  it("keeps Server/LAN distinct from a local provider at the same endpoint", () => {
    const endpoint = "http://127.0.0.1:1234/v1";
    const serverContract = resolveRuntimePanelContract({
      mode: "server",
      serverUrl: endpoint,
      transformerModel: "gpt-oss-20b",
      serverRuntime: {
        reachable: true,
        loaded_model: "gpt-oss-20b",
        models: ["gpt-oss-20b"],
      },
      providerRuntime: {
        base_url: endpoint,
        server_running: true,
        model_loaded: true,
        loaded_model: "gpt-oss-20b",
      },
    });
    const providerContract = resolveRuntimePanelContract({
      mode: "local",
      localModel: "lmstudio",
      providerMode: "remote-unmanaged",
      providerModels: ["gpt-oss-20b"],
      providerRuntime: {
        base_url: endpoint,
        effective_model_id: "gpt-oss-20b",
        inventory_reachable: true,
        capabilities: { start_stop: false },
      },
    });

    expect(serverContract).toMatchObject({
      lane: RUNTIME_PANEL_LANES.SERVER_LAN,
      model: "gpt-oss-20b",
      endpoint,
      availability: RUNTIME_AVAILABILITY.USABLE,
    });
    expect(providerContract).toMatchObject({
      lane: RUNTIME_PANEL_LANES.LOCAL_PROVIDER,
      model: "gpt-oss-20b",
      endpoint,
      availability: RUNTIME_AVAILABILITY.USABLE,
    });
  });

  it("degrades a configured Server/LAN model missing from live inventory", () => {
    expect(
      resolveRuntimePanelContract({
        mode: "server",
        serverUrl: "http://127.0.0.1:1234/v1",
        serverModel: "configured-but-missing",
        serverRuntime: {
          reachable: true,
          loaded_model: "actually-loaded",
          models: ["actually-loaded"],
        },
      }),
    ).toMatchObject({
      model: "actually-loaded",
      availability: RUNTIME_AVAILABILITY.DEGRADED,
    });
  });

  it.each([
    [
      {
        mode: "server",
        serverUrl: "http://127.0.0.1:1234/v1",
        serverLoading: true,
      },
      RUNTIME_AVAILABILITY.CHECKING,
    ],
    [
      {
        mode: "server",
        serverUrl: "http://127.0.0.1:1234/v1",
        serverRuntime: { reachable: true },
      },
      RUNTIME_AVAILABILITY.DEGRADED,
    ],
    [
      {
        mode: "server",
        transformerModel: "gpt-oss-20b",
        serverRuntime: { reachable: true },
      },
      RUNTIME_AVAILABILITY.UNAVAILABLE,
    ],
  ])("maps Server/LAN readiness to the shared vocabulary", (input, availability) => {
    expect(resolveRuntimePanelContract(input).availability).toBe(availability);
  });

  it("marks a managed provider without a loaded model as degraded", () => {
    expect(
      resolveRuntimePanelContract({
        mode: "local",
        transformerModel: "lmstudio",
        providerRuntime: {
          provider: "lmstudio",
          server_running: true,
          model_loaded: false,
          preferred_model: "google/gemma-4-12b",
          base_url: "http://127.0.0.1:1234/v1",
        },
      }),
    ).toMatchObject({
      lane: RUNTIME_PANEL_LANES.LOCAL_PROVIDER,
      model: "google/gemma-4-12b",
      endpoint: "http://127.0.0.1:1234/v1",
      availability: RUNTIME_AVAILABILITY.DEGRADED,
    });
  });

  it("marks a cached provider model state as degraded without erasing its model", () => {
    expect(
      resolveRuntimePanelContract({
        mode: "local",
        localModel: "lmstudio",
        providerMode: "remote-unmanaged",
        providerRuntime: {
          base_url: "http://127.0.0.1:1234/v1",
          server_running: true,
          model_loaded: true,
          chat_ready: true,
          loaded_model: "openai/gpt-oss-20b",
          model_state_known: false,
          model_state_source: "cache",
          model_state_stale: true,
          capabilities: { start_stop: false },
        },
      }),
    ).toMatchObject({
      model: "openai/gpt-oss-20b",
      availability: RUNTIME_AVAILABILITY.DEGRADED,
    });
  });

  it.each([
    [
      {
        mode: "local",
        localModel: "ollama",
        providerRuntime: { installed: true, server_running: false },
      },
      RUNTIME_AVAILABILITY.UNAVAILABLE,
    ],
    [
      {
        mode: "local",
        localModel: "ollama",
        providerLoading: true,
      },
      RUNTIME_AVAILABILITY.CHECKING,
    ],
    [
      {
        mode: "local",
        localModel: "ollama",
        providerRuntime: {
          server_running: true,
          model_loaded: true,
          loaded_model: "gpt-oss-20b",
        },
      },
      RUNTIME_AVAILABILITY.USABLE,
    ],
  ])("maps local-provider readiness to the shared vocabulary", (input, availability) => {
    expect(resolveRuntimePanelContract(input).availability).toBe(availability);
  });

  it("normalizes a ready direct-local runtime without a provider endpoint", () => {
    expect(
      resolveRuntimePanelContract({
        mode: "local",
        localModel: "gemma-4-E2B-it",
        runtime: {
          effective_model_id: "gemma-4-E2B-it",
          load_state: "ready",
          loaded: true,
        },
      }),
    ).toEqual({
      lane: RUNTIME_PANEL_LANES.DIRECT_LOCAL,
      model: "gemma-4-E2B-it",
      endpoint: "",
      availability: RUNTIME_AVAILABILITY.USABLE,
      lastOperation: null,
    });
  });

  it.each([
    [
      {
        mode: "local",
        localModel: "gemma-4-E2B-it",
        runtime: { model: "gemma-4-E2B-it", load_state: "loading" },
      },
      RUNTIME_AVAILABILITY.CHECKING,
    ],
    [
      {
        mode: "local",
        localModel: "gemma-4-E2B-it",
        runtime: { model: "gemma-4-E2B-it", load_state: "idle" },
      },
      RUNTIME_AVAILABILITY.DEGRADED,
    ],
    [
      {
        mode: "local",
        localModel: "gemma-4-E2B-it",
        runtime: {
          model: "gemma-4-E2B-it",
          preflight: { ready: false },
        },
      },
      RUNTIME_AVAILABILITY.UNAVAILABLE,
    ],
  ])("maps direct-local readiness to the shared vocabulary", (input, availability) => {
    expect(resolveRuntimePanelContract(input).availability).toBe(availability);
  });

  it("normalizes the selected lane's latest meaningful operation", () => {
    const now = 2_000_000;
    const contract = resolveRuntimePanelContract(
      {
        mode: "local",
        localModel: "lmstudio",
        providerRuntime: {
          server_running: true,
          model_loaded: true,
          loaded_model: "gpt-oss-20b",
          last_operation: {
            id: "load#7",
            action: "load",
            status: "ok",
            model: "gpt-oss-20b",
            started_at: 1_998,
            finished_at: 1_999,
            duration_ms: 842,
          },
        },
      },
      now,
    );

    expect(contract.lastOperation).toMatchObject({
      label: expect.stringContaining("load#7 ok for gpt-oss-20b"),
      status: "ok",
    });
  });
});
