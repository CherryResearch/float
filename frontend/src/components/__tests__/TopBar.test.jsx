import React from "react";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve({ data: { devices: [], default_device: null } });
      }
      if (url === "/api/models/registered") {
        return Promise.resolve({ data: { models: [] } });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              installed: true,
              server_running: true,
              model_loaded: false,
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

  it("shows local/lmstudio and local/ollama in local model select", async () => {
    renderTopBar({ localModel: "lmstudio" });

    expect((await screen.findAllByRole("combobox")).length).toBeGreaterThan(0);
    expect(screen.getByRole("option", { name: "local/lmstudio" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "local/ollama" })).toBeInTheDocument();
  });

  it("checks provider runtime status endpoint when local runtime marker is selected", async () => {
    renderTopBar({ localModel: "lmstudio" });

    await waitFor(() => {
      expect(axiosMocks.get).toHaveBeenCalledWith("/api/llm/provider/status", {
        params: { provider: "lmstudio" },
      });
    });
  });

  it("persists local provider when selecting a runtime marker", async () => {
    renderTopBar({ localModel: "lmstudio" });

    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "ollama" } });

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith("/api/settings", {
        transformer_model: "ollama",
        local_provider: "ollama",
      });
    });
  });
});
