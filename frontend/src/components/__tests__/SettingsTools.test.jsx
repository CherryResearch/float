import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import { vi } from "vitest";
import axios from "axios";

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {
        wsStatus: "online",
        apiProviderStatus: "online",
        approvalLevel: "all",
        transformerModel: "gpt-oss-20b",
        staticModel: "gpt-4o-mini",
        harmonyFormat: false,
        serverUrl: "",
        sttModel: "whisper-1",
        ttsModel: "tts-1",
        voiceModel: "alloy",
        visionModel: "clip-vit-base-patch32",
        maxContextLength: 2048,
        kvCache: true,
        ramSwap: false,
        apiModels: ["gpt-4o-mini"],
        apiModel: "gpt-4o-mini",
      },
      setState: vi.fn(),
    }),
  };
});

vi.mock("../ModelJobsPanel", () => ({
  default: () => <div data-testid="model-jobs-panel" />,
}));

import Settings from "../Settings";
import { GlobalContext } from "../../main";

const baseState = {
  wsStatus: "online",
  apiProviderStatus: "online",
  approvalLevel: "all",
  transformerModel: "gpt-oss-20b",
  staticModel: "gpt-4o-mini",
  harmonyFormat: false,
  serverUrl: "",
  sttModel: "whisper-1",
  ttsModel: "tts-1",
  voiceModel: "alloy",
  visionModel: "clip-vit-base-patch32",
  maxContextLength: 2048,
  kvCache: true,
  ramSwap: false,
  apiModels: ["gpt-4o-mini"],
  apiModel: "gpt-4o-mini",
};

let settingsResponse;

const renderWithState = (stateOverrides = {}) => {
  const setState = vi.fn();
  const state = { ...baseState, ...stateOverrides };
  return render(
    <MemoryRouter>
      <GlobalContext.Provider value={{ state, setState }}>
        <Settings />
      </GlobalContext.Provider>
    </MemoryRouter>,
  );
};

describe("Settings tools browser", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    settingsResponse = {
      mode: "api",
      model: "gpt-4o-mini",
      transformer_model: "gpt-oss-20b",
      static_model: "gpt-4o-mini",
      stt_model: "whisper-1",
      tts_model: "tts-1",
      voice_model: "alloy",
      vision_model: "clip-vit-base-patch32",
      api_key_set: false,
      hf_token_set: false,
      devices: [],
    };
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve({ data: settingsResponse });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({
          data: {
            tool_resolution_notifications: true,
            action_history_retention_days: 7,
            sync_link_to_source_device: false,
            sync_source_namespace: "",
          },
        });
      }
      if (url === "/api/tools/catalog") {
        return Promise.resolve({
          data: {
            tools: [
              {
                id: "search_web",
                display_name: "Web Search",
                status: "live",
                category: "web",
                origin: "builtin",
                summary: "Search public web results and return titles, links, and snippets.",
                runtime: { executor: "backend_python", network: true, filesystem: false },
                can_access: ["public search results from supported providers"],
                limit_hints: ["`max_results` is capped at 10."],
              },
              {
                id: "open_url",
                display_name: "Open URL",
                status: "stub",
                category: "web",
                origin: "builtin",
                summary: "Placeholder browser-open tool.",
                runtime: { executor: "backend_python", network: false, filesystem: false },
                can_access: ["the provided URL string only"],
                limit_hints: ["Stub behavior only; no browser handoff yet."],
              },
            ],
          },
        });
      }
      if (url === "/api/tools/limits") {
        return Promise.resolve({
          data: {
            roots: {
              data: "D:/float/data",
              workspace: "D:/float/data/workspace",
            },
            limits: {
              search_web_max_results: 10,
              crawl_response_chars: 10000,
              list_dir_max_entries: 200,
            },
          },
        });
      }
      if (url === "/api/health" || url === "/health") {
        return Promise.resolve({ data: { status: "healthy" } });
      }
      if (url === "/api/mcp/status") {
        return Promise.resolve({
          data: {
            provider: "fastmcp",
            reachable: true,
            url: "http://127.0.0.1:8123/mcp",
          },
        });
      }
      if (url === "/api/rag/status") {
        return Promise.resolve({
          data: {
            backend: "chroma",
            exists: true,
            writable: true,
            documents: 0,
            size_bytes: 0,
            files: 0,
          },
        });
      }
      if (url === "/api/celery/status") {
        return Promise.resolve({ data: { online: false, workers: [] } });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              provider: "lmstudio",
              server_running: true,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              context_length: 8192,
              base_url: "http://127.0.0.1:1234/v1",
            },
          },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b", "qwen2.5-coder-7b-instruct"],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders the built-in tool browser and filters entries", async () => {
    renderWithState();

    expect(await screen.findByText("Web Search")).toBeInTheDocument();
    expect(screen.getByText("Open URL")).toBeInTheDocument();
    expect(screen.getByText("D:/float/data/workspace")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter tools"), {
      target: { value: "stub" },
    });

    await waitFor(() => {
      expect(screen.getByText("Open URL")).toBeInTheDocument();
      expect(screen.queryByText("Web Search")).not.toBeInTheDocument();
    });
  });

  it("persists the tool review notification toggle", async () => {
    renderWithState();

    const checkbox = await screen.findByLabelText("Notify when tools need review");
    expect(checkbox).toBeChecked();

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        tool_resolution_notifications: false,
      });
    });
  });

  it("narrows settings sections through search and restores them when cleared", async () => {
    renderWithState();

    expect(await screen.findByText("Workspace & Tools")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: /search settings/i }), {
      target: { value: "live transcript" },
    });

    await waitFor(() => {
      expect(
        screen.getByText('Showing 1 of 7 sections for "live transcript".'),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Models & Retrieval")).toBeInTheDocument();
    expect(screen.queryByText("Workspace & Tools")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear search/i }));

    await waitFor(() => {
      expect(screen.getByText("Workspace & Tools")).toBeInTheDocument();
    });
  });

  it("shows tool-source status and a no-results state for unmatched filters", async () => {
    renderWithState();

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(screen.getByText("Connected source")).toBeInTheDocument();
    expect(screen.getByText("Custom tools")).toBeInTheDocument();
    expect(screen.getByText("MCP bridge is reachable from Settings.")).toBeInTheDocument();
    expect(screen.getAllByText("http://127.0.0.1:8123/mcp").length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Filter tools"), {
      target: { value: "no-such-tool" },
    });

    await waitFor(() => {
      expect(screen.getByText('No tools match "no-such-tool".')).toBeInTheDocument();
    });
  });

  it("saves the work history retention window", async () => {
    renderWithState();

    const retentionSelect = await screen.findByLabelText(
      /how long reversible history is kept/i,
    );
    expect(retentionSelect).toHaveValue("7");

    fireEvent.change(retentionSelect, { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: /save work history/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        action_history_retention_days: 14,
      });
    });
    expect(await screen.findByText(/Work history retention saved\./i)).toBeInTheDocument();
  });

  it("labels tool display controls clearly and explains the console fallback", async () => {
    renderWithState({
      toolDisplayMode: "console",
      toolLinkBehavior: "inline",
    });

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: /where tool details appear/i }),
    ).toHaveValue("console");
    expect(
      screen.getByRole("combobox", { name: /when a tool link is clicked in chat/i }),
    ).toHaveValue("inline");
    expect(screen.getByRole("option", { name: "Agent console" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Inline in chat" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /Current behavior: clicking a tool link opens the agent console because tool details are set to appear there\./i,
      ),
    ).toBeInTheDocument();
  });

  it("explains when inline tool links expand chat cards", async () => {
    renderWithState({
      toolDisplayMode: "inline",
      toolLinkBehavior: "inline",
    });

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Current behavior: clicking a tool link expands the matching inline tool card in chat\./i,
      ),
    ).toBeInTheDocument();
  });

  it("scopes CUDA controls to direct local runtimes and shows provider inventory", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "lmstudio",
      local_provider: "lmstudio",
      devices: [{ id: "cuda:0", type: "cuda", name: "RTX 4090", total_memory_gb: 24 }],
      cuda_diagnostics: {
        status: "degraded",
        cuda_available: false,
        note: "GPU detected but the current PyTorch build lacks CUDA support.",
      },
    };

    const { container } = renderWithState({
      transformerModel: "lmstudio",
    });

    expect(await screen.findByText("External provider compatibility (LM Studio / Ollama)")).toBeInTheDocument();
    expect(
      screen.getByText(/Direct Transformers checkpoints are the primary local runtime path\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Device and CUDA controls only apply when `Local Language Model` points/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("Inference Device")).not.toBeInTheDocument();
    expect(screen.getByText("Loaded: gpt-oss-20b")).toBeInTheDocument();
    expect(screen.getByText("2 provider models reported.")).toBeInTheDocument();

    const preferredInput = container.querySelector(
      'input[name="local_provider_preferred_model"]',
    );
    expect(preferredInput).not.toBeNull();
    expect(preferredInput).toHaveAttribute("list", "provider-model-options");
    expect(container.querySelectorAll("#provider-model-options option")).toHaveLength(2);
  });

  it("offers neat provider bridge actions from settings", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "lmstudio",
      local_provider: "lmstudio",
      local_provider_preferred_model: "gpt-oss-20b",
      local_provider_default_context_length: 8192,
      devices: [],
    };
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/llm/provider/load") {
        return Promise.resolve({ data: { status: "success", ok: true, payload } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithState({ transformerModel: "lmstudio" });

    expect(await screen.findByText("Provider bridge runtime")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /load preferred/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/llm/provider/load", {
        provider: "lmstudio",
        model: "gpt-oss-20b",
        context_length: 8192,
      });
    });
    expect(await screen.findByText(/Provider load requested for gpt-oss-20b\./i)).toBeInTheDocument();
  });

  it("previews instance sync sections and starts a pull", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/plan") {
        return Promise.resolve({
          data: {
            link_to_source: true,
            effective_namespaces: {
              pull: "laptop",
              push: "desktop",
            },
            remote: { base_url: "http://peer.float:5000" },
            sections: [
              {
                key: "conversations",
                label: "Conversations",
                remote_newer: 2,
                local_newer: 1,
                only_remote: 1,
                only_local: 0,
                identical: 4,
                selected_by_default: true,
              },
              {
                key: "settings",
                label: "Workspace preferences",
                remote_newer: 1,
                local_newer: 0,
                only_remote: 0,
                only_local: 0,
                identical: 0,
                selected_by_default: true,
              },
            ],
            pull_sections: [
              {
                key: "conversations",
                label: "Conversations",
                remote_newer: 2,
                local_newer: 1,
                only_remote: 1,
                only_local: 0,
                identical: 4,
                selected_by_default: true,
                items: [
                  { resource_id: "conv-a", label: "Alpha", status: "remote_newer" },
                  { resource_id: "conv-b", label: "Beta", status: "only_remote" },
                ],
              },
              {
                key: "settings",
                label: "Workspace preferences",
                remote_newer: 1,
                local_newer: 0,
                only_remote: 0,
                only_local: 0,
                identical: 0,
                selected_by_default: true,
                items: [
                  { resource_id: "settings", label: "Workspace preferences", status: "remote_newer" },
                ],
              },
            ],
            push_sections: [
              {
                key: "conversations",
                label: "Conversations",
                remote_newer: 0,
                local_newer: 3,
                only_remote: 0,
                only_local: 1,
                identical: 4,
                selected_by_default: true,
                items: [
                  { resource_id: "conv-a", label: "Alpha", status: "local_newer" },
                ],
              },
              {
                key: "settings",
                label: "Workspace preferences",
                remote_newer: 0,
                local_newer: 1,
                only_remote: 0,
                only_local: 0,
                identical: 0,
                selected_by_default: true,
                items: [
                  { resource_id: "settings", label: "Workspace preferences", status: "local_newer" },
                ],
              },
            ],
          },
        });
      }
      if (url === "/api/sync/apply") {
        return Promise.resolve({
          data: {
            effective_namespace: "laptop",
            result: {
              sections: {
                conversations: { applied: 2, skipped: 1 },
                settings: { applied: 1, skipped: 0 },
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithState();

    fireEvent.change(await screen.findByLabelText("Remote Float URL"), {
      target: { value: "http://peer.float:5000" },
    });
    fireEvent.click(
      screen.getByLabelText(/link synced data to its source device\/workspace/i),
    );
    fireEvent.change(screen.getByLabelText("This device label / namespace"), {
      target: { value: "desktop" },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview sync/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/sync/plan", {
        remote_url: "http://peer.float:5000",
        link_to_source: true,
        source_namespace: "desktop",
      });
    });

    expect(await screen.findByRole("dialog", { name: /sync preview/i })).toBeInTheDocument();
    expect(screen.getByText(/Pull here will link remote data under/i)).toBeInTheDocument();
    expect(screen.getByText(/laptop\//i)).toBeInTheDocument();
    expect(screen.getByText(/Push there will link this instance under/i)).toBeInTheDocument();
    expect(screen.getByText(/desktop\//i)).toBeInTheDocument();
    expect(screen.getByText(/Pull here: Remote newer: 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Push there: Remote newer: 0 \| Local newer: 3/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Pull item preview/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Alpha/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Only remote/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /pull here/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/sync/apply", {
        remote_url: "http://peer.float:5000",
        direction: "pull",
        sections: ["conversations", "settings"],
        link_to_source: true,
        source_namespace: "desktop",
      });
    });
    expect(await screen.findByText(/Pull complete\./i)).toBeInTheDocument();
    expect(screen.getByText(/Stored under laptop\//i)).toBeInTheDocument();
  });

  it("saves sync defaults from the settings panel", async () => {
    renderWithState();

    fireEvent.click(
      await screen.findByLabelText(/link synced data to its source device\/workspace/i),
    );
    fireEvent.change(screen.getByLabelText("This device label / namespace"), {
      target: { value: "desktop" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save sync defaults/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        sync_link_to_source_device: true,
        sync_source_namespace: "desktop",
      });
    });
    expect(await screen.findByText(/Sync defaults saved\./i)).toBeInTheDocument();
  });
});
