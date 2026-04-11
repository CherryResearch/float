import React from "react";
import { vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("axios", () => ({
  default: axiosMocks,
}));

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {},
      setState: () => {},
    }),
  };
});

import { GlobalContext } from "../../main";
import TopBar from "../TopBar";

const originalFetch = global.fetch;

const baseState = {
  backendMode: "local",
  apiStatus: "online",
  apiProviderStatus: "online",
  approvalLevel: "all",
  apiModel: "gpt-5",
  localModel: "lmstudio",
  transformerModel: "gpt-oss-20b",
  sessionName: "Test Session",
  theme: "dark",
  wsStatus: "online",
  wsLastEventAt: null,
  wsLastError: "",
  wsLastErrorAt: null,
  apiModels: [],
  devices: [],
  defaultDevice: null,
  inferenceDevice: null,
  registeredLocalModels: [],
  serverUrl: "",
};

const renderTopBar = (stateOverrides = {}) => {
  const setState = vi.fn();
  const state = { ...baseState, ...stateOverrides };
  render(
    <MemoryRouter>
      <GlobalContext.Provider value={{ state, setState }}>
        <TopBar />
      </GlobalContext.Provider>
    </MemoryRouter>,
  );
  return { setState, state };
};

describe("TopBar local runtime entries", () => {
  beforeEach(() => {
    axiosMocks.get.mockReset();
    axiosMocks.post.mockReset();
    global.fetch = vi.fn();
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve({ data: { devices: [], default_device: null } });
      }
      if (url === "/api/models/registered") {
        return Promise.resolve({ data: { models: [] } });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b"],
            runtime: {
              installed: true,
              server_running: true,
              model_loaded: false,
              preferred_model: "gpt-oss-20b",
            },
          },
        });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              installed: true,
              server_running: true,
              model_loaded: false,
              preferred_model: "gpt-oss-20b",
            },
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });
    if (typeof window.matchMedia !== "function") {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      });
    }
  });

  afterEach(() => {
    if (originalFetch) {
      global.fetch = originalFetch;
    } else {
      delete global.fetch;
    }
  });

  it("shows local runtime markers in the local model select", async () => {
    renderTopBar({ localModel: "lmstudio" });

    expect((await screen.findAllByRole("combobox")).length).toBeGreaterThan(0);
    expect(screen.getByRole("option", { name: "local/lmstudio" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "local/ollama" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "local/openai-compatible" }),
    ).toBeInTheDocument();
  });

  it("checks provider runtime status endpoint when local runtime marker is selected", async () => {
    renderTopBar({ localModel: "lmstudio" });

    await waitFor(() => {
      expect(axiosMocks.get).toHaveBeenCalledWith("/api/llm/provider/models", {
        params: { provider: "lmstudio" },
      });
    });
  });

  it("polls provider snapshots so local status catches up after a model loads", async () => {
    let providerModelPolls = 0;
    let providerStatusPolls = 0;
    const intervalCallbacks = [];
    vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
      if (delay === 60000) {
        intervalCallbacks.push(callback);
      }
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => {});
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve({ data: { devices: [], default_device: null } });
      }
      if (url === "/api/models/registered") {
        return Promise.resolve({ data: { models: [] } });
      }
      if (url === "/api/llm/provider/models") {
        providerModelPolls += 1;
        return Promise.resolve({
          data: {
            models: ["gemma4:e4b"],
            runtime: {
              installed: true,
              server_running: true,
              model_loaded: false,
              preferred_model: "gemma4:e4b",
            },
          },
        });
      }
      if (url === "/api/llm/provider/status") {
        providerStatusPolls += 1;
        return Promise.resolve({
          data: {
            runtime:
              providerStatusPolls < 1
                ? {
                    installed: true,
                    server_running: true,
                    model_loaded: false,
                    preferred_model: "gemma4:e4b",
                  }
                : {
                    installed: true,
                    server_running: true,
                    model_loaded: true,
                    loaded_model: "gemma4:e4b",
                    effective_model_id: "gemma4:e4b",
                  },
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });

    renderTopBar({ localModel: "ollama" });

    await waitFor(() => {
      expect(providerModelPolls).toBe(1);
    });
    expect(screen.getByLabelText("Backend status")).toHaveAttribute(
      "title",
      "Local (on-device): degraded",
    );
    expect(intervalCallbacks).toHaveLength(1);

    await act(async () => {
      intervalCallbacks[0]();
    });

    await waitFor(() => {
      expect(providerStatusPolls).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByLabelText("Backend status")).toHaveAttribute(
      "title",
      "Local (on-device): ready",
    );
  });

  it("persists local provider when selecting a runtime marker", async () => {
    renderTopBar({ localModel: "lmstudio" });

    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "ollama" } });

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith("/api/settings", {
        local_provider: "ollama",
      });
    });
  });

  it("persists backend mode changes instead of only updating local state", async () => {
    renderTopBar({
      backendMode: "api",
      apiModel: "gpt-5",
      localModel: "lmstudio",
    });

    fireEvent.click(screen.getByRole("button", { name: "Backend mode" }));

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith("/api/settings", {
        mode: "local",
        local_provider: "lmstudio",
      });
    });
  });

  it("keeps server model selection visible and probes model inventory endpoints", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    renderTopBar({
      backendMode: "server",
      localModel: "lmstudio",
      transformerModel: "gpt-oss-20b",
      serverUrl: "http://127.0.0.1:1234",
    });

    expect(screen.getByPlaceholderText("server/lan url")).toHaveValue(
      "http://127.0.0.1:1234",
    );
    const select = await screen.findByRole("combobox");
    expect(select).toHaveDisplayValue("gpt-oss-20b");

    fireEvent.change(select, { target: { value: "Llama-3.1-8B" } });

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith("/api/settings", {
        transformer_model: "Llama-3.1-8B",
      });
    });
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "http://127.0.0.1:1234/v1/models",
        { method: "GET" },
      );
    });
    const calledUrls = global.fetch.mock.calls.map(([url]) => url);
    expect(calledUrls).not.toContain("http://127.0.0.1:1234/health");
    expect(calledUrls).not.toContain("http://127.0.0.1:1234");
  });
});
