import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import { vi } from "vitest";
import axios from "axios";

const modelJobsPanelMock = vi.hoisted(() => ({
  mounts: 0,
  unmounts: 0,
}));

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {
        wsStatus: "online",
        apiProviderStatus: "online",
        approvalLevel: "all",
        transformerModel: "gpt-oss-20b",
        staticModel: "gpt-5.4-mini",
        harmonyFormat: false,
        harmonyFormatMode: "auto",
        serverUrl: "",
        sttModel: "whisper-1",
        ttsModel: "tts-1",
        voiceModel: "alloy",
        visionModel: "clip-vit-base-patch32",
        maxContextLength: 2048,
        kvCache: true,
        ramSwap: false,
        apiModels: ["gpt-5.4", "gpt-5.4-mini"],
        apiModel: "gpt-5.4",
      },
      setState: vi.fn(),
    }),
  };
});

vi.mock("../ModelJobsPanel", () => ({
  default: () => {
    const React = require("react");
    React.useEffect(() => {
      modelJobsPanelMock.mounts += 1;
      return () => {
        modelJobsPanelMock.unmounts += 1;
      };
    }, []);
    return <div data-testid="model-jobs-panel" />;
  },
}));

import Settings from "../Settings";
import { GlobalContext } from "../../main";

const baseState = {
  wsStatus: "online",
  apiProviderStatus: "online",
  approvalLevel: "all",
  transformerModel: "gpt-oss-20b",
  staticModel: "gpt-5.4-mini",
  harmonyFormat: false,
  harmonyFormatMode: "auto",
  serverUrl: "",
  sttModel: "whisper-1",
  ttsModel: "tts-1",
  voiceModel: "alloy",
  visionModel: "clip-vit-base-patch32",
  maxContextLength: 2048,
  kvCache: true,
  ramSwap: false,
  apiModels: ["gpt-5.4", "gpt-5.4-mini"],
  apiModel: "gpt-5.4",
  visualTheme: "spring",
  workflowProfile: "default",
  captureRetentionDays: 7,
  captureDefaultSensitivity: "personal",
  captureAllowModelRawImageAccess: true,
  captureAllowSummaryFallback: true,
  enabledWorkflowModules: ["computer_use"],
};

let settingsResponse;
let captionStatusResponse;

const renderWithState = (options = {}) => {
  const normalized =
    options && (Object.prototype.hasOwnProperty.call(options, "stateOverrides") ||
      Object.prototype.hasOwnProperty.call(options, "setState"))
      ? options
      : { stateOverrides: options };
  const stateOverrides = normalized.stateOverrides || {};
  const setState = normalized.setState || vi.fn();
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
    modelJobsPanelMock.mounts = 0;
    modelJobsPanelMock.unmounts = 0;
    settingsResponse = {
      mode: "api",
      model: "gpt-5.4",
      transformer_model: "gpt-oss-20b",
      static_model: "gpt-5.4-mini",
      harmony_format: true,
      harmony_format_mode: "auto",
      visual_theme: "spring",
      stt_model: "whisper-1",
      tts_model: "tts-1",
      voice_model: "alloy",
      vision_model: "clip-vit-base-patch32",
      api_key_set: false,
      hf_token_set: false,
      devices: [],
      background_autonomy_enabled: false,
      background_autonomy_sandbox_processes: true,
      background_autonomy_mode: "overnight",
      background_autonomy_interval_seconds: 900,
      background_autonomy_max_reflections_per_tick: 1,
      background_autonomy_max_runtime_seconds: 1800,
      background_autonomy_satisfied_threshold: 0.8,
      background_autonomy_basic_tick_count: 2,
      background_autonomy_basic_tick_seconds: 300,
      background_autonomy_min_priority: 0.05,
    };
    captionStatusResponse = {
      engine: "local",
      configured_model: "google/paligemma2-3b-pt-224",
      ready: false,
      automatic_downloads: false,
      local: {
        model: "google/paligemma2-3b-pt-224",
        weights_available: false,
        installed: false,
        dependencies_available: true,
        can_attempt: false,
        loaded: false,
        loadable: false,
        reason: "model_weights_unavailable",
      },
      cloud: {
        provider: "openai-compatible",
        model: "gpt-5.4-nano",
        configured: false,
        api_url_configured: true,
        api_key_set: false,
      },
    };
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "put").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "delete").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve({ data: settingsResponse });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({
          data: {
            tool_resolution_notifications: true,
            action_history_retention_days: 7,
            capture_retention_days: 7,
            capture_default_sensitivity: "personal",
            capture_allow_model_raw_image_access: true,
            capture_allow_summary_fallback: true,
            default_workflow: "default",
            enabled_workflow_modules: ["computer_use"],
          },
        });
      }
      if (url === "/api/themes") {
        return Promise.resolve({
          data: {
            themes: [
              {
                id: "forest-glass",
                label: "Forest Glass",
                slots: {
                  c1Light: "#d6f5dd",
                  c1Med: "#3c8f5a",
                  c1Dark: "#173927",
                  c2Light: "#f4efc7",
                  c2Med: "#c6a93e",
                  c2Dark: "#5e4b12",
                  veryLight: "#fcfff8",
                  veryDark: "#08110a",
                },
              },
            ],
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
                policy: {
                  workflow: "both",
                  approval: "low",
                  workflows: { text: true, live: true },
                  live_auto: true,
                },
              },
              {
                id: "open_url",
                display_name: "Open URL",
                status: "legacy",
                category: "web",
                origin: "builtin",
                summary: "Legacy alias for browser navigation through the shared computer runtime.",
                runtime: { executor: "backend_python", network: true, filesystem: false },
                can_access: ["browser navigation requests routed through the computer runtime"],
                limit_hints: ["Legacy alias; prefer computer.navigate for new work."],
              },
              {
                id: "computer.observe",
                display_name: "Observe Computer",
                status: "live",
                category: "computer",
                origin: "builtin",
                summary: "Capture a screenshot and summary for the current session.",
                runtime: { executor: "backend_python", network: false, filesystem: true },
                can_access: ["computer session screenshots and metadata"],
                limit_hints: ["Requires an active computer session."],
              },
              {
                id: "computer.act",
                display_name: "Act On Computer",
                status: "live",
                category: "computer",
                origin: "builtin",
                summary: "Apply one or more browser or desktop actions.",
                runtime: { executor: "backend_python", network: false, filesystem: false },
                can_access: ["the active computer session only"],
                limit_hints: ["Mutating actions require approval."],
              },
              {
                id: "computer.navigate",
                display_name: "Navigate Browser",
                status: "live",
                category: "computer",
                origin: "builtin",
                summary: "Navigate the active browser computer session.",
                runtime: { executor: "backend_python", network: true, filesystem: false },
                can_access: ["the active browser session only"],
                limit_hints: ["Requires an active browser session."],
              },
              {
                id: "computer.windows.list",
                display_name: "List Windows",
                status: "experimental",
                category: "computer",
                origin: "builtin",
                summary: "List visible desktop windows.",
                runtime: { executor: "backend_python", network: false, filesystem: false },
                can_access: ["visible desktop windows on the host"],
                limit_hints: ["Windows runtime only."],
              },
              {
                id: "computer.app.launch",
                display_name: "Launch Desktop App",
                status: "experimental",
                category: "computer",
                origin: "builtin",
                summary: "Launch a desktop application in the Windows runtime.",
                runtime: { executor: "backend_python", network: false, filesystem: false },
                can_access: ["allowed desktop applications on the host"],
                limit_hints: ["Windows runtime only."],
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
            embedding_runtime: {
              model: "local:all-MiniLM-L6-v2",
              mode: "sentence_transformer",
              state: "idle",
              loaded: false,
              init_attempted: false,
              error: null,
            },
          },
        });
      }
      if (url === "/api/attachments/caption/status") {
        return Promise.resolve({ data: captionStatusResponse });
      }
      if (url === "/api/celery/status") {
        return Promise.resolve({ data: { online: false, workers: [] } });
      }
      if (url === "/api/background/autonomy/status") {
        return Promise.resolve({
          data: {
            autonomy: {
              enabled: false,
              sandbox_processes: true,
              mode: "manual",
              configured_mode: "overnight",
              routine_enabled: false,
              max_runtime_seconds: 1800,
              satisfied_threshold: 0.8,
              basic_tick_count: 2,
              basic_tick_seconds: 300,
              max_reflections_per_tick: 1,
              reflection: { candidate_count: 1 },
              session: {
                mode: "overnight",
                runtime_budget_seconds: 1800,
                stop_reason: null,
              },
            },
          },
        });
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
            runtime: {
              provider: "lmstudio",
              server_running: true,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              effective_model_id: "gpt-oss-20b",
              base_url: "http://127.0.0.1:1234/v1",
              checked_at: Math.floor(Date.now() / 1000) - 6,
            },
          },
        });
      }
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: {
            runtime: {
              active_backend: "transformers",
              effective_model_id: "gemma-4-E2B-it",
              load_state: "ready",
              load_finished_at: Math.floor(Date.now() / 1000) - 5,
              local_loader: "image_text_to_text",
              supports_images: true,
              preflight: {
                ready: false,
                python_executable: "D:/notebooks/float_dev/backend/.venv/Scripts/python.exe",
                missing_packages: ["torch", "transformers"],
                missing_runtime_components: [],
                hint:
                  "Direct local loading uses the backend Python at 'D:/notebooks/float_dev/backend/.venv/Scripts/python.exe', but this environment is missing torch, transformers.",
              },
            },
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
      target: { value: "legacy" },
    });

    await waitFor(() => {
      expect(screen.getByText("Open URL")).toBeInTheDocument();
      expect(screen.queryByText("Web Search")).not.toBeInTheDocument();
    });
  });

  it("saves per-tool workflow and approval policies", async () => {
    renderWithState();

    const card = (await screen.findByText("Web Search")).closest(".tool-browser-card");
    expect(card).not.toBeNull();
    const workflowSelect = within(card).getByRole("combobox", {
      name: /web search availability/i,
    });
    const approvalSelect = within(card).getByRole("combobox", {
      name: /web search approval requirement/i,
    });
    expect(workflowSelect).toHaveValue("both");
    expect(within(workflowSelect).getByRole("option", { name: "Chat + live voice" }))
      .toBeInTheDocument();
    expect(approvalSelect).toHaveValue("low");
    expect(within(approvalSelect).getByRole("option", { name: "Lower approval" }))
      .toBeInTheDocument();

    fireEvent.change(workflowSelect, { target: { value: "text" } });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        tool_policies: {
          search_web: {
            workflow: "text",
            approval: "low",
          },
        },
      });
    });
    expect(await screen.findByText(/Web Search tool policy saved\./i)).toBeInTheDocument();
  });

  it("makes service outages readable in the status panel", async () => {
    renderWithState({
      stateOverrides: {
        wsStatus: "offline",
        wsLastError: "connection closed unexpectedly",
        wsLastErrorAt: Date.now() - 45000,
      },
    });

    expect(
      await screen.findByText(
        /API, websocket, background queue, provider bridge, and storage health\./i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Live thought stream not connected.", {
        selector: ".status-note--primary",
      }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Background queue unavailable")).toBeInTheDocument();
    expect(
      screen.getByText("Background jobs will not start until a Celery worker responds."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Worker controls appear when the queue is reachable or tasks exist."),
    ).not.toBeInTheDocument();
  });

  it("names the review-all tool approval mode consistently", async () => {
    renderWithState();

    expect(
      await screen.findByRole("combobox", { name: /tool approval mode/i }),
    ).toHaveDisplayValue("Review all");
  });

  it("keeps recent websocket drops in a reconnecting state", async () => {
    renderWithState({
      stateOverrides: {
        wsStatus: "offline",
        wsLastEventAt: Date.now() - 5000,
      },
    });

    expect(
      await screen.findByText("Reconnecting to live thought stream.", {
        selector: ".status-note--primary",
      }),
    ).toBeInTheDocument();
  });

  it("explains when a provider server is running outside Float", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "lmstudio",
      local_provider: "lmstudio",
      local_provider_mode: "local-managed",
      local_provider_preferred_model: "gpt-oss-20b",
    };
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              provider: "lmstudio",
              installed: false,
              server_running: true,
              server_owned_by_float: false,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              loaded_model_owned_by_float: false,
              context_length: 8192,
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: true,
                load_unload: true,
                context_length: true,
              },
            },
          },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b"],
            runtime: {
              provider: "lmstudio",
              installed: false,
              server_running: true,
              server_owned_by_float: false,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              loaded_model_owned_by_float: false,
              effective_model_id: "gpt-oss-20b",
              checked_at: Math.floor(Date.now() / 1000) - 6,
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: true,
                load_unload: true,
                context_length: true,
              },
            },
          },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    renderWithState({
      stateOverrides: {
        transformerModel: "lmstudio",
      },
    });

    expect(await screen.findByText("Loaded outside Float: gpt-oss-20b")).toBeInTheDocument();
    expect(
      screen.getByText(/switch this lane to External HTTP only before using start, stop, load, unload, or delete here\./i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Explain provider runtime state"));
    const inspector = screen.getByRole("dialog", {
      name: "Why this provider runtime is shown",
    });
    expect(within(inspector).getByText("Owner")).toBeInTheDocument();
    expect(within(inspector).getByText("outside Float")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: /^visual data\./i })).toBeInTheDocument();

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

  it("shows the runtime server URL when searching for server", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "server",
      server_url: "http://127.0.0.1:1234/v1",
    };

    renderWithState();

    expect(await screen.findByText("Connections & Access")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: /search settings/i }), {
      target: { value: "server" },
    });

    await waitFor(() => {
      expect(screen.getByText("Language Runtime Connections")).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue("http://127.0.0.1:1234/v1")).toHaveAttribute(
      "name",
      "server_url",
    );
    expect(
      screen.queryByRole("button", { name: /edit server\/lan url/i }),
    ).not.toBeInTheDocument();
  });

  it("saves Harmony Formatting as a backend tri-state mode", async () => {
    renderWithState();

    const modeSelect = await screen.findByLabelText("Mode", {
      selector: "#harmony-mode-select",
    });
    expect(modeSelect).toHaveValue("auto");

    fireEvent.change(modeSelect, { target: { value: "disabled" } });
    const saveButton = screen.getByRole("button", { name: /^save$/i });
    await waitFor(() => expect(saveButton).not.toBeDisabled());
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          harmony_format_mode: "disabled",
          harmony_format: false,
        }),
      );
    });
  });

  it("hides a missing selected model when downloaded-only is enabled", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "mistral:7b",
      tts_model: "kitten",
      voice_model: "expr-voice-2-f",
    };
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/transformers/models") {
        return Promise.resolve({ data: { models: ["kitten"] } });
      }
      if (String(url).includes("/api/models/exists/mistral%3A7b")) {
        return Promise.resolve({ data: { exists: false } });
      }
      if (String(url).includes("/api/models/exists/kitten")) {
        return Promise.resolve({ data: { exists: true } });
      }
      if (String(url).includes("/api/models/info/kitten")) {
        return Promise.resolve({
          data: {
            repo_id: "KittenML/kitten-tts-nano-0.1",
            downloadable: true,
            lane: "local",
          },
        });
      }
      return defaultGet(url, ...rest);
    });

    renderWithState();

    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    const checkbox = await screen.findByLabelText(/downloaded only/i);
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(screen.getAllByText("Choose a downloaded model").length).toBeGreaterThan(0);
    });
    const languageModelSelect = screen.getByLabelText("Language Model");
    expect(languageModelSelect).toHaveValue("");
    expect(
      Array.from(document.querySelectorAll(".status-note.warn.form-note")).some((node) => {
        const text = node.textContent || "";
        return (
          text.includes("Saved selection") &&
          text.includes("mistral:7b") &&
          text.includes("not downloaded or registered")
        );
      }),
    ).toBe(true);
    expect(
      within(languageModelSelect).queryByRole("option", { name: /mistral:7b/i }),
    ).not.toBeInTheDocument();
  }, 20000);

  it("uses the in-panel section rail to scroll within settings", async () => {
    renderWithState();

    expect(await screen.findByRole("heading", { name: "settings" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /output & prompting/i })).toBeInTheDocument();

    const container = document.querySelector(".settings-container");
    const toolbar = document.querySelector(".settings-topbar");
    container.scrollTo = vi.fn();
    Object.defineProperty(toolbar, "offsetHeight", {
      value: 92,
      configurable: true,
    });

    const outputSection = document.getElementById("settings-output");
    Object.defineProperty(outputSection, "offsetTop", {
      value: 720,
      configurable: true,
    });

    fireEvent.click(screen.getByRole("button", { name: /^output/i }));

    expect(container.scrollTo).toHaveBeenCalledWith({
      top: 610,
      behavior: "smooth",
    });
    expect(screen.getByRole("button", { name: /^output/i })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("does not remount the model jobs panel on parent rerender", async () => {
    const setState = vi.fn();
    const state = { ...baseState };
    const view = render(
      <MemoryRouter>
        <GlobalContext.Provider value={{ state, setState }}>
          <Settings />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("model-jobs-panel")).toBeInTheDocument();
    expect(modelJobsPanelMock.mounts).toBe(1);
    expect(modelJobsPanelMock.unmounts).toBe(0);

    view.rerender(
      <MemoryRouter>
        <GlobalContext.Provider value={{ state, setState }}>
          <Settings />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("model-jobs-panel")).toBeInTheDocument();
    expect(modelJobsPanelMock.mounts).toBe(1);
    expect(modelJobsPanelMock.unmounts).toBe(0);
  });

  it("defers the model library scan until the models section is active", async () => {
    renderWithState();

    expect(await screen.findByRole("heading", { name: "settings" })).toBeInTheDocument();
    expect(
      axios.get.mock.calls.filter(([url]) => url === "/api/transformers/models"),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /^models\./i }));

    await waitFor(() => {
      expect(
        axios.get.mock.calls.filter(([url]) => url === "/api/transformers/models"),
      ).toHaveLength(1);
    });
  });

  it("shows honest local caption readiness separately from CLIP indexing", async () => {
    renderWithState();

    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    const panel = (await screen.findByRole("heading", { name: "Saved image captions" }))
      .closest(".settings-caption-engine");

    expect(
      within(panel).getByRole("button", { name: /^local captioning:/i }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByText("Not ready")).toBeInTheDocument();
    expect(
      within(panel).getByText(
        "Configured model: google/paligemma2-3b-pt-224",
      ),
    ).toBeInTheDocument();
    expect(within(panel).getByText("CLIP indexing: separate, local"))
      .toBeInTheDocument();
    expect(within(panel).getByText("Caption model weights were not found on this device."))
      .toBeInTheDocument();
    expect(
      within(panel).getByLabelText(/About saved image captions:.*CLIP image indexing/i),
    ).toBeInTheDocument();
    expect(
      within(panel).getByRole("link", { name: /open gallery to retry one image/i }),
    ).toHaveAttribute("href", "/knowledge?tab=documents");
  }, 15000);

  it("keeps cloud captioning inactive until the explicit mode is saved", async () => {
    renderWithState();

    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    const panel = (await screen.findByRole("heading", { name: "Saved image captions" }))
      .closest(".settings-caption-engine");
    fireEvent.click(
      within(panel).getByRole("button", { name: /^cloud\/provider captioning:/i }),
    );

    expect(within(panel).getByText(/Saved engine: Local/i)).toBeInTheDocument();
    expect(
      within(panel).getByText(
        /If you save Cloud\/provider, caption requests will send saved image bytes/i,
      ),
    ).toHaveTextContent(/It is not active yet/i);
    expect(within(panel).getByLabelText("Cloud caption model"))
      .toHaveValue("gpt-5.4-nano");

    const saveButton = screen.getByRole("button", { name: /^save$/i });
    await waitFor(() => expect(saveButton).not.toBeDisabled());
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          image_caption_engine: "cloud",
          image_caption_cloud_model: "gpt-5.4-nano",
        }),
      );
    });
  }, 15000);

  it("distinguishes an installed local caption model from a loaded runtime", async () => {
    captionStatusResponse = {
      ...captionStatusResponse,
      ready: false,
      can_generate: true,
      can_attempt: true,
      local: {
        ...captionStatusResponse.local,
        weights_available: true,
        installed: true,
        configured: true,
        can_attempt: true,
        loaded: false,
        verified: false,
        loadable: false,
        reason: "model_installed_not_loaded",
      },
    };
    renderWithState();

    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    const panel = (await screen.findByRole("heading", { name: "Saved image captions" }))
      .closest(".settings-caption-engine");

    expect(within(panel).getByText("Installed; loads on use")).toBeInTheDocument();
    expect(
      within(panel).getByText(/installed locally but has not been loaded yet/i),
    ).toHaveTextContent(/first caption request loads it/i);
  }, 15000);

  it("labels cloud egress as active only when cloud mode is persisted", async () => {
    settingsResponse = {
      ...settingsResponse,
      image_caption_engine: "cloud",
      image_caption_cloud_model: "gpt-5.4-nano",
    };
    captionStatusResponse = {
      ...captionStatusResponse,
      engine: "cloud",
      configured_model: "gpt-5.4-nano",
      ready: true,
      cloud: {
        provider: "openai-compatible",
        model: "gpt-5.4-nano",
        configured: true,
        api_url_configured: true,
        api_key_set: true,
      },
    };

    renderWithState();
    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    const panel = (await screen.findByRole("heading", { name: "Saved image captions" }))
      .closest(".settings-caption-engine");

    expect(
      within(panel).getByRole("button", { name: /^cloud\/provider captioning:/i }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByText("Ready")).toBeInTheDocument();
    expect(within(panel).getByText(/Saved engine: Cloud\/provider/i))
      .toBeInTheDocument();
    expect(within(panel).getByText(/Cloud captioning is saved/i))
      .toHaveTextContent(/saved image bytes/i);
  }, 15000);

  it("adds a Hugging Face model link to the personal model catalog", async () => {
    const entry = {
      alias: "my-model",
      repo_id: "acme/example-model",
      url: "https://huggingface.co/acme/example-model",
      model_type: "transformer",
      runtime: "direct",
      source_type: "huggingface",
    };
    let registeredModels = [];
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/models/registered") {
        return Promise.resolve({ data: { models: registeredModels } });
      }
      return defaultGet(url, ...rest);
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/models/registered/huggingface") {
        registeredModels = [entry];
        return Promise.resolve({ data: { model: entry } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithState();
    fireEvent.click(await screen.findByRole("button", { name: /^models\./i }));
    expect(
      screen.queryByPlaceholderText("https://huggingface.co/owner/model or owner/model"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add model" }));

    fireEvent.change(
      screen.getByPlaceholderText("https://huggingface.co/owner/model or owner/model"),
      { target: { value: "https://huggingface.co/acme/example-model" } },
    );
    fireEvent.change(screen.getByPlaceholderText("Alias (optional)"), {
      target: { value: "my-model" },
    });
    fireEvent.click(
      screen.getByTitle("Add Hugging Face model to personal catalog"),
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/models/registered/huggingface",
        {
          url: "https://huggingface.co/acme/example-model",
          alias: "my-model",
          model_type: "transformer",
          runtime: "direct",
        },
      );
    });
    expect(await screen.findByText("acme/example-model")).toBeInTheDocument();
    expect(
      screen.getByText("Added 'my-model' from acme/example-model."),
    ).toBeInTheDocument();
  });

  it("uses an API lane for language models and shows discovered endpoint models", async () => {
    renderWithState();

    const select = await screen.findByLabelText("Language Model");
    const block = select.closest(".settings-model-block");

    expect(within(block).getByRole("button", { name: "API" })).toBeInTheDocument();
    expect(within(block).queryByRole("button", { name: /server \/ lan/i })).not.toBeInTheDocument();
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(
      expect.arrayContaining(["gpt-5.4 (API)", "gpt-5.4-mini (API)"]),
    );

    fireEvent.click(within(block).getByRole("button", { name: "Local" }));

    await waitFor(() => {
      expect(select.value).toBe("gpt-oss-20b");
    });
  });

  it("renders the rolling OpenAI API alias with its resolved display target", async () => {
    settingsResponse = {
      ...settingsResponse,
      model: "chat-latest",
    };

    renderWithState({
      stateOverrides: {
        apiModel: "chat-latest",
        apiModels: ["chat-latest", "gpt-5.5", "gpt-5.5-2026-07-01"],
        apiModelAliases: {
          "chat-latest": {
            label: "GPT latest",
            target_model: "gpt-5.5",
          },
        },
      },
    });

    const select = await screen.findByLabelText("Language Model");

    expect(select.value).toBe("chat-latest");
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(
      expect.arrayContaining(["GPT latest (gpt-5.5) (API)"]),
    );
  });

  it("treats realtime whisper as an API STT model", async () => {
    settingsResponse = {
      ...settingsResponse,
      stt_model: "gpt-realtime-whisper",
    };

    renderWithState();

    const select = await screen.findByLabelText("STT Model");
    const block = select.closest(".settings-model-block");

    expect(within(block).getByRole("button", { name: "API" })).toHaveClass(
      "is-active",
    );
    expect(select.value).toBe("gpt-realtime-whisper");
    expect(select.querySelector('option[value="gpt-realtime-whisper"]')).not.toBeNull();
  });

  it("exposes background autonomy budgets and queues a dry-run tick", async () => {
    renderWithState();

    expect(
      await screen.findByRole("heading", { name: /background processing/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Run bounded routine reflection reviews on a timer/i))
      .toBeInTheDocument();
    expect(
      screen.getByLabelText(/request sandboxing for background processes/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/enable background autonomy/i));
    fireEvent.click(
      screen.getByLabelText(/request sandboxing for background processes/i),
    );
    fireEvent.change(screen.getByLabelText(/legacy supervisor preset/i), {
      target: { value: "extended" },
    });
    const advancedLimits = screen.getByText(/advanced limits/i).closest("details");
    expect(advancedLimits).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText(/advanced limits/i));
    expect(advancedLimits).toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText(/runtime budget \(minutes\)/i), {
      target: { value: "45" },
    });
    fireEvent.change(screen.getByLabelText(/satisfied threshold/i), {
      target: { value: "0.85" },
    });

    const saveButton = screen.getByRole("button", {
      name: /save background settings/i,
    });
    await waitFor(() => expect(saveButton).not.toBeDisabled());
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          background_autonomy_enabled: true,
          background_autonomy_mode: "extended",
          background_autonomy_max_runtime_seconds: 2700,
          background_autonomy_satisfied_threshold: 0.85,
          background_autonomy_basic_tick_count: 2,
          background_autonomy_basic_tick_seconds: 300,
        }),
      );
    });
    const savedPayload = axios.post.mock.calls.find(([url]) => url === "/api/settings")?.[1];
    expect(savedPayload).not.toHaveProperty("background_autonomy_sandbox_processes");

    fireEvent.click(screen.getByRole("button", { name: /dry run tick/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/background/autonomy/tick",
        expect.objectContaining({
          mode: "extended",
          dry_run: true,
          max_runtime_seconds: 2700,
          satisfied_threshold: 0.85,
        }),
      );
    });
  }, 30000);

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

  it("saves capture and privacy settings without rewriting workflow defaults", async () => {
    const setState = vi.fn();
    renderWithState({
      stateOverrides: {
        workflowProfile: "default",
        enabledWorkflowModules: ["computer_use"],
      },
      setState,
    });

    expect(await screen.findByText("Visual Data & Privacy", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Open Skills & workflows", { selector: "a" })).toHaveAttribute(
      "href",
      "/knowledge?tab=skills",
    );

    fireEvent.change(screen.getByLabelText(/how long transient captures are kept/i), {
      target: { value: "14" },
    });
    fireEvent.change(screen.getByLabelText(/default capture sensitivity/i), {
      target: { value: "protected" },
    });
    fireEvent.click(screen.getByLabelText(/allow raw image access for supported models/i));
    fireEvent.click(
      screen.getByText("Save capture & privacy settings", { selector: "button" }),
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        capture_retention_days: 14,
        capture_default_sensitivity: "protected",
        capture_allow_model_raw_image_access: false,
        capture_allow_summary_fallback: true,
        privacy_filter_mode: "off",
        privacy_filter_model: "openai/privacy-filter",
        privacy_filter_route_private_mode: "off",
      });
    });
    expect(setState).toHaveBeenCalled();
    expect(
      await screen.findByText(/capture and privacy settings saved/i),
    ).toBeInTheDocument();
  }, 30000);

  it("summarizes browser and Windows computer-use tools in settings", async () => {
    renderWithState();

    expect(await screen.findByText("Computer use")).toBeInTheDocument();
    expect(screen.getByText("Browser tools: 4")).toBeInTheDocument();
    expect(screen.getByText("Windows tools: 2")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Windows desktop control is available as an experimental runtime/i,
      ),
    ).toBeInTheDocument();
  });

  it("saves the work history retention window", async () => {
    renderWithState();

    const retentionSelect = await screen.findByLabelText(
      /how long reversible history is kept/i,
    );
    expect(retentionSelect).toHaveValue("7");

    fireEvent.change(retentionSelect, { target: { value: "14" } });
    fireEvent.click(screen.getByText("Save work history", { selector: "button" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        action_history_retention_days: 14,
      });
    });
    expect(await screen.findByText(/Work history retention saved\./i)).toBeInTheDocument();
  }, 60000);

  it("labels tool display controls clearly and explains the console fallback", async () => {
    renderWithState({
      toolDisplayMode: "console",
      toolLinkBehavior: "inline",
    });

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: /where tool details appear/i }),
    ).toHaveValue("console");
    const displayModeSelect = screen.getByRole("combobox", {
      name: /where tool details appear/i,
    });
    expect(
      screen.getByRole("combobox", { name: /when a tool link is clicked in chat/i }),
    ).toHaveValue("inline");
    expect(within(displayModeSelect).getByRole("option", { name: "Agent console" })).toBeInTheDocument();
    expect(within(displayModeSelect).getByRole("option", { name: "Inline in chat" })).toBeInTheDocument();
    expect(within(displayModeSelect).getByRole("option", { name: "Both" })).toBeInTheDocument();
    expect(within(displayModeSelect).getByRole("option", { name: "Auto" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /Current behavior: clicking a tool link opens the agent console because tool details are set to appear there\./i,
      ),
    ).toBeInTheDocument();
  }, 10000);

  it("offers a visual theme selector in settings", async () => {
    const ThemeHarness = () => {
      const [state, setState] = React.useState({
        ...baseState,
        visualTheme: "spring",
      });
      return (
        <MemoryRouter>
          <GlobalContext.Provider value={{ state, setState }}>
            <Settings />
          </GlobalContext.Provider>
        </MemoryRouter>
      );
    };

    render(<ThemeHarness />);

    const select = await screen.findByLabelText("Visual theme");
    expect(select).toHaveValue("spring");
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(
      expect.arrayContaining([
        "Spring (built-in)",
        "Blossom (built-in)",
        "Ash (built-in)",
        "Cappucino (built-in)",
        "Sunset Citrus (built-in)",
        "Midnight Plum (built-in)",
        "Forest Glass (custom)",
      ]),
    );

    fireEvent.change(select, { target: { value: "sunset-citrus" } });

    await waitFor(() => {
      expect(select).toHaveValue("sunset-citrus");
    });
  }, 20000);

  it("creates and deletes a custom theme from settings", async () => {
    const ThemeHarness = () => {
      const [state, setState] = React.useState({
        ...baseState,
        visualTheme: "spring",
        customThemes: [],
      });
      return (
        <MemoryRouter>
          <GlobalContext.Provider value={{ state, setState }}>
            <Settings />
          </GlobalContext.Provider>
        </MemoryRouter>
      );
    };

    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/themes") {
        return Promise.resolve({
          data: {
            status: "saved",
            theme: {
              id: payload.id || "custom-sunrise",
              label: payload.label,
              slots: payload.slots,
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    vi.spyOn(axios, "delete").mockResolvedValue({ data: { status: "deleted" } });

    render(<ThemeHarness />);

    await screen.findByRole("option", { name: "Forest Glass (custom)" });
    fireEvent.click(screen.getByText("Add new theme", { selector: "button" }));
    fireEvent.change(screen.getByLabelText("Theme name"), {
      target: { value: "Custom Sunrise" },
    });
    fireEvent.click(screen.getByText("Save theme", { selector: "button" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/themes", expect.objectContaining({
        id: null,
        label: "Custom Sunrise",
      }));
    });
    expect(await screen.findByText("Theme saved.")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Delete theme", { selector: "button" }));

    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/themes/custom-sunrise");
    });
    expect(await screen.findByText("Theme deleted.")).toBeInTheDocument();
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

  it("explains both mode without hiding the console timeline", async () => {
    renderWithState({
      toolDisplayMode: "both",
      toolLinkBehavior: "inline",
    });

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Current behavior: clicking a tool link expands the matching inline tool card in chat, and the agent console still keeps the same tool activity available\./i,
      ),
    ).toBeInTheDocument();
  });

  it("explains auto mode as selected-message and streaming aware", async () => {
    renderWithState({
      toolDisplayMode: "auto",
      toolLinkBehavior: "console",
    });

    expect(await screen.findByText("Built-in tools")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Auto keeps tool details inline for the selected or highlighted message, and while the current response is streaming\./i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Current behavior: clicking a tool link focuses the matching item in the agent console, while auto mode still shows inline cards for the active or streaming message\./i,
      ),
    ).toBeInTheDocument();
  });

  it("shows the shared Cloud API runtime summary in the language card", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "api",
      model: "gpt-5.4",
      api_url: "https://api.example.test/v1/responses",
    };

    renderWithState({
      backendMode: "api",
      apiModel: "gpt-5.4",
      apiStatus: "online",
      apiProviderStatus: "online",
    });

    const summary = await screen.findByRole("group", {
      name: "Language runtime summary",
    });
    expect(within(summary).getByText("Lane: Cloud API")).toBeInTheDocument();
    expect(within(summary).getByText("Model: gpt-5.4")).toBeInTheDocument();
    expect(
      within(summary).getByText(
        "Endpoint: https://api.example.test/v1/responses",
      ),
    ).toBeInTheDocument();
    expect(within(summary).getByText("Availability: usable")).toBeInTheDocument();
  });

  it("probes and summarizes the selected Server/LAN runtime lane", async () => {
    const serverUrl = "http://127.0.0.1:1234/v1";
    settingsResponse = {
      ...settingsResponse,
      mode: "server",
      transformer_model: "gpt-oss-20b",
      server_url: serverUrl,
    };
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/llm/server/models") {
        return Promise.resolve({
          data: {
            reachable: true,
            loaded_model: "gpt-oss-20b",
            models: ["gpt-oss-20b", "server-inventory-alternative"],
          },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    const { container } = renderWithState({
      backendMode: "server",
      transformerModel: "gpt-oss-20b",
      serverUrl,
    });

    const summary = await screen.findByRole("group", {
      name: "Language runtime summary",
    });
    expect(within(summary).getByText("Lane: Server/LAN")).toBeInTheDocument();
    expect(within(summary).getByText("Model: gpt-oss-20b")).toBeInTheDocument();
    expect(within(summary).getByText(`Endpoint: ${serverUrl}`)).toBeInTheDocument();
    await waitFor(() => {
      expect(within(summary).getByText("Availability: usable")).toBeInTheDocument();
    });
    expect(axios.get).toHaveBeenCalledWith("/api/llm/server/models", {
      params: { server_url: serverUrl },
    });
    expect(
      container.querySelector(
        '#server-runtime-model-options option[value="server-inventory-alternative"]',
      ),
    ).toBeInTheDocument();
  });

  it("warns when a manually configured preset targets Grok", async () => {
    const xaiUrl = "https://api.x.ai/v1";
    settingsResponse = {
      ...settingsResponse,
      mode: "server",
      transformer_model: "grok-test",
      server_url: xaiUrl,
      server_preset_id: "custom-risky-endpoint",
      server_presets: [
        {
          id: "lm-studio-local",
          name: "LM Studio (localhost:1234)",
          provider: "lmstudio",
          base_url: "http://127.0.0.1:1234/v1",
          builtin: true,
        },
        {
          id: "custom-risky-endpoint",
          name: "Risky endpoint",
          provider: "xai",
          base_url: xaiUrl,
          api_key_env: "XAI_API_KEY",
          api_key_set: false,
          builtin: false,
        },
      ],
    };
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/llm/server/models") {
        return Promise.resolve({
          data: { reachable: true, models: ["grok-test"], trust_warning: true },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    renderWithState({ backendMode: "server", serverUrl: xaiUrl });

    const presetSelect = await screen.findByRole("combobox", {
      name: "Server connection preset",
    });
    expect(presetSelect).toHaveValue("custom-risky-endpoint");
    expect(
      screen.getByText("XAI_API_KEY is not set in the Float process environment."),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This model may not be trustworthy.",
    );
    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith("/api/llm/server/models", {
        params: { server_url: xaiUrl, preset_id: "custom-risky-endpoint" },
      });
    });
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

    expect(
      await screen.findByText(/External provider compatibility \(LM Studio \/ Ollama \/ OpenAI-compatible\)/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Direct Transformers checkpoints are the primary local runtime path\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Device and CUDA controls only apply when `Local Language Model` points/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("Inference Device")).not.toBeInTheDocument();
    expect(screen.getByText("Loaded: gpt-oss-20b")).toBeInTheDocument();
    expect(screen.getByText(/2 provider models reported\./i)).toBeInTheDocument();

    const preferredInput = container.querySelector(
      'input[name="local_provider_preferred_model"]',
    );
    expect(preferredInput).not.toBeNull();
    expect(preferredInput).toHaveAttribute("list", "provider-model-options");
    expect(container.querySelectorAll("#provider-model-options option")).toHaveLength(2);
  });

  it("polls provider runtime quietly in settings and keeps freshness in a tooltip", async () => {
    const intervalDelays = [];
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "lmstudio",
      local_provider: "lmstudio",
      devices: [],
    };
    vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
      intervalDelays.push(delay);
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => {});

    renderWithState({ transformerModel: "lmstudio" });

    expect(await screen.findByText("Provider bridge runtime")).toBeInTheDocument();
    expect(screen.getByText("Loaded: gpt-oss-20b")).toBeInTheDocument();
    expect(screen.getByText(/2 provider models reported\./i)).toBeInTheDocument();
    expect(screen.queryByText(/Updated /i)).not.toBeInTheDocument();
    expect(intervalDelays).toContain(60000);

    const freshnessIndicator = screen.getByLabelText("Provider inventory freshness");
    expect(freshnessIndicator).toHaveAttribute(
      "title",
      expect.stringContaining("Automatic provider refresh runs about once per minute."),
    );
  });

  it("shows the last provider action in settings without requiring raw logs", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "lmstudio",
      local_provider: "lmstudio",
      devices: [],
    };
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
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
              last_operation: {
                id: "unload#3",
                action: "unload",
                status: "ok",
                model: "gpt-oss-20b",
                started_at: Math.floor(Date.now() / 1000) - 7,
                finished_at: Math.floor(Date.now() / 1000) - 6,
                duration_ms: 412,
                result: {
                  note: "Unloaded requested model",
                  endpoint: "http://127.0.0.1:1234/v1/responses",
                },
              },
            },
          },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b", "qwen2.5-coder-7b-instruct"],
            runtime: {
              provider: "lmstudio",
              server_running: true,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              effective_model_id: "gpt-oss-20b",
              base_url: "http://127.0.0.1:1234/v1",
              checked_at: Math.floor(Date.now() / 1000) - 6,
              last_operation: {
                id: "unload#3",
                action: "unload",
                status: "ok",
                model: "gpt-oss-20b",
                started_at: Math.floor(Date.now() / 1000) - 7,
                finished_at: Math.floor(Date.now() / 1000) - 6,
                duration_ms: 412,
                result: {
                  note: "Unloaded requested model",
                  endpoint: "http://127.0.0.1:1234/v1/responses",
                },
              },
            },
          },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    renderWithState({ transformerModel: "lmstudio" });

    expect(await screen.findByText("Provider bridge runtime")).toBeInTheDocument();
    const lastAction = await screen.findByText(
      /Last action: unload#3 ok for gpt-oss-20b/i,
    );
    expect(lastAction).toBeInTheDocument();
    expect(lastAction).toHaveAttribute(
      "title",
      expect.stringContaining("Unloaded requested model"),
    );

    const collapseButton = screen
      .getAllByRole("button", { name: "Collapse" })
      .find(
        (button) =>
          button.getAttribute("aria-controls") ===
          "settings-language-runtime-details",
      );
    expect(collapseButton).toBeTruthy();
    const runtimePanel = collapseButton.closest(".runtime-inline-panel");
    expect(runtimePanel).not.toBeNull();
    fireEvent.click(collapseButton);

    expect(collapseButton).toHaveAttribute("aria-expanded", "false");
    expect(
      within(runtimePanel).queryByText(/^Loaded model:/i),
    ).not.toBeInTheDocument();
    const summary = within(runtimePanel).getByRole("group", {
      name: "Language runtime summary",
    });
    expect(within(summary).getByText("Lane: Local provider")).toBeInTheDocument();
    expect(within(summary).getByText("Model: gpt-oss-20b")).toBeInTheDocument();
    expect(
      within(summary).getByText("Endpoint: http://127.0.0.1:1234/v1"),
    ).toBeInTheDocument();
    expect(within(summary).getByText("Availability: usable")).toBeInTheDocument();

    const compactLastAction = within(runtimePanel).getByLabelText(
      "Latest runtime operation",
    );
    expect(compactLastAction).toHaveTextContent(
      /Last action: unload#3 ok for gpt-oss-20b/i,
    );
    expect(compactLastAction).toHaveAttribute(
      "title",
      expect.stringContaining("Unloaded requested model"),
    );
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
    expect(
      (await screen.findAllByText(/Provider load requested for gpt-oss-20b\./i)).length,
    ).toBeGreaterThan(0);
  });

  it("surfaces Gemma 4 E2B as the direct local suggestion without suggesting larger provider-first Gemma 4 variants", async () => {
    renderWithState();

    const select = await screen.findByLabelText("Language Model");
    const block = select.closest(".settings-model-block");
    fireEvent.click(within(block).getByRole("button", { name: "Local" }));

    expect(await screen.findByRole("option", { name: /gemma-4-E2B-it.*direct local/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /gemma-4-E4B-it/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /gemma-4-31B-it/i })).not.toBeInTheDocument();
  });

  it("shows capability badges and the EmbeddingGemma preset in the models section", async () => {
    renderWithState();

    expect(await screen.findByRole("option", { name: /EmbeddingGemma 300M/i })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Text generation").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("Speech synthesis").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("Image embeddings").length).toBeGreaterThan(0);
  });

  it("keeps Gemma 4 E2B in the local lane and shows direct runtime details", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "gemma-4-E2B-it",
      devices: [{ id: "cuda:0", type: "cuda", name: "RTX 4070", total_memory_gb: 12 }],
      cuda_diagnostics: {
        status: "online",
        cuda_available: true,
      },
    };

    const { container } = renderWithState({
      localModel: "gemma-4-E2B-it",
      transformerModel: "gemma-4-E2B-it",
    });

    expect(await screen.findByDisplayValue("gemma-4-E2B-it")).toBeInTheDocument();
    expect(
      screen.getByText(/Direct local Transformers runtime for the selected language model\./i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Backend: transformers/i)).toBeInTheDocument();
    expect(screen.getByText(/loader: image_text_to_text/i)).toBeInTheDocument();
    expect(screen.getByText(/Loaded .*ago\./i)).toBeInTheDocument();
    expect(
      screen.getByText(/Backend Python: D:\/notebooks\/float_dev\/backend\/\.venv\/Scripts\/python\.exe/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Missing direct-local packages: torch, transformers\./i),
    ).toBeInTheDocument();

    const collapseButton = screen
      .getAllByRole("button", { name: "Collapse" })
      .find(
        (button) =>
          button.getAttribute("aria-controls") ===
          "settings-language-runtime-details",
      );
    expect(collapseButton).toBeTruthy();
    const runtimePanel = collapseButton.closest(".runtime-inline-panel");
    expect(runtimePanel).not.toBeNull();
    fireEvent.click(collapseButton);
    expect(collapseButton).toHaveAttribute("aria-expanded", "false");
    expect(
      runtimePanel.querySelector("#settings-language-runtime-details"),
    ).not.toBeInTheDocument();

    const summary = within(runtimePanel).getByRole("group", {
      name: "Language runtime summary",
    });
    expect(within(summary).getByText("Lane: Direct local")).toBeInTheDocument();
    expect(within(summary).getByText("Model: gemma-4-E2B-it")).toBeInTheDocument();
    expect(within(summary).getByText("Availability: unavailable")).toBeInTheDocument();
    expect(
      within(summary).queryByLabelText(/^Runtime endpoint:/i),
    ).not.toBeInTheDocument();

    const laneRow = container
      .querySelector("#settings-model-transformer_model")
      ?.closest(".model-select-row");
    expect(laneRow).toHaveClass("model-lane-local");
  });

  it("blocks direct local runtime load when preflight is not ready", async () => {
    settingsResponse = {
      ...settingsResponse,
      mode: "local",
      transformer_model: "gemma-4-E2B-it",
    };

    renderWithState({
      localModel: "gemma-4-E2B-it",
      transformerModel: "gemma-4-E2B-it",
    });

    expect(await screen.findByDisplayValue("gemma-4-E2B-it")).toBeInTheDocument();

    const loadButton = screen
      .getAllByRole("button", { name: /^Load$/i })
      .find((button) =>
        button.getAttribute("title")?.match(/missing torch, transformers/i),
      );
    expect(loadButton).toBeTruthy();
    expect(loadButton).toBeDisabled();
    expect(loadButton).toHaveAttribute(
      "title",
      expect.stringMatching(/missing torch, transformers/i),
    );

    fireEvent.click(loadButton);
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/llm/load-local",
      expect.anything(),
    );
  });

  it("shows an embeddings runtime loading state while the local encoder is being loaded", async () => {
    let resolveLoad;
    axios.post.mockImplementation((url) => {
      if (url === "/api/rag/embeddings/load") {
        return new Promise((resolve) => {
          resolveLoad = resolve;
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithState();

    expect(await screen.findByText("Embeddings runtime")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Load$/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Loading\.\.\./i })).toBeDisabled();
    });
    expect(screen.getByText(/load…/i)).toBeInTheDocument();

    resolveLoad({
      data: {
        embedding_runtime: {
          model: "local:all-MiniLM-L6-v2",
          mode: "sentence_transformer",
          state: "loaded",
          loaded: true,
          init_attempted: true,
          error: null,
        },
      },
    });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/rag/embeddings/load");
    });
    expect(await screen.findByText(/Embedding runtime loaded\./i)).toBeInTheDocument();
  });

  it("warns when speech model ids are placed in TTS and live voice fields", async () => {
    settingsResponse = {
      ...settingsResponse,
      voice_model: "voxtral-small-24b-2507",
      realtime_voice: "voxtral-small-24b-2507",
    };

    renderWithState();

    expect(
      await screen.findByText(/speech model id, not a TTS voice preset/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/not a supported OpenAI Realtime voice/i),
    ).toBeInTheDocument();
  });

  it("uses model-specific TTS voice preset options", async () => {
    settingsResponse = {
      ...settingsResponse,
      tts_model: "gpt-4o-mini-tts",
      voice_model: "marin",
    };

    renderWithState();

    expect(await screen.findByText("Speech")).toBeInTheDocument();
    const optionValues = Array.from(
      document.querySelectorAll("#voice-preset-options option"),
    ).map((option) => option.getAttribute("value"));
    expect(optionValues).toContain("marin");
    expect(optionValues).toContain("cedar");
  });

  it("switches live streaming between local and api lanes", async () => {
    settingsResponse = {
      ...settingsResponse,
      stream_backend: "local",
      live_agent_mode: "server",
      live_agent_model: "gemma-4-E4B-it",
      live_multimodal_model: "gemma-4-26B-A4B-it",
      realtime_model: "gpt-realtime-2.1",
      realtime_voice: "cedar",
    };

    renderWithState();

    const heading = await screen.findByRole("heading", { name: "Live streaming" });
    const block = heading.closest(".settings-subcard");

    expect(block).not.toBeNull();
    expect(
      within(block).getByRole("button", { name: "Local" }),
    ).toHaveClass("is-active");
    expect(within(block).getByDisplayValue("gemma-4-E4B-it")).toBeInTheDocument();
    expect(
      within(block).getByDisplayValue("gemma-4-26B-A4B-it"),
    ).toBeInTheDocument();
    expect(within(block).getByText("Live agent mode")).toBeInTheDocument();
    expect(
      within(block).getByLabelText(/local live connection details/i),
    ).toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button", { name: "API" }));

    expect(await within(block).findByDisplayValue("gpt-realtime-2.1")).toBeInTheDocument();
    expect(block.querySelector('option[value="gpt-realtime-2.1-mini"]')).not.toBeNull();
    expect(block.querySelector('option[value="gpt-realtime-mini"]')).not.toBeNull();
    expect(block.querySelector('option[value="gpt-realtime-1.5"]')).not.toBeNull();
    expect(within(block).getByDisplayValue("cedar")).toBeInTheDocument();
    expect(within(block).queryByText("Live agent mode")).not.toBeInTheDocument();
  });

  it("uses Knowledge Sync as the single sharing and sync entry point", async () => {
    renderWithState();

    const links = await screen.findAllByRole("link", {
      name: /open knowledge sync/i,
    });

    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/knowledge?tab=sync");
    expect(screen.queryByLabelText("Remote Float URL")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /preview sync/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /save sync defaults/i }),
    ).not.toBeInTheDocument();
  });
});
