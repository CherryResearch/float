import React from "react";
import { vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import axios from "axios";

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {
        backendMode: "api",
        apiStatus: "online",
        approvalLevel: "all",
        apiModel: "test-model",
        transformerModel: "gpt-oss-20b",
        localModel: "local-model",
        selectedCalendarDate: new Date("2024-01-01T00:00:00Z"),
        calendarEvents: [],
        sessionId: "sess-123",
      },
      setState: vi.fn(),
    }),
  };
});

import AgentConsole from "../AgentConsole";
import ActionHistoryPanel from "../ActionHistoryPanel";
import { GlobalContext } from "../../main";
import { TOOL_REVIEW_ACTION_EVENT } from "../../utils/toolReviewActions";

const slugify = (input) =>
  (input || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
    .slice(0, 48);

const baseGlobalState = {
  backendMode: "api",
  apiStatus: "online",
  approvalLevel: "all",
  apiModel: "test-model",
  transformerModel: "gpt-oss-20b",
  localModel: "local-model",
  userTimezone: "",
  selectedCalendarDate: new Date("2024-01-01T00:00:00Z"),
  calendarEvents: [],
  sessionId: "sess-123",
};

const renderWithGlobalState = (
  ui,
  { stateOverrides = {}, setState = vi.fn() } = {},
) => {
  const state = { ...baseGlobalState, ...stateOverrides };
  return render(
    <MemoryRouter>
      <GlobalContext.Provider value={{ state, setState }}>
        {ui}
      </GlobalContext.Provider>
    </MemoryRouter>,
  );
};

const expandRuntimeDetails = async () => {
  fireEvent.click(
    await screen.findByRole("button", { name: /expand runtime details/i }),
  );
};

describe("AgentConsole", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: {
            runtime: {
              mode: "local",
              memory: { gpu: [], system: {} },
              load_state: "ready",
              load_finished_at: Math.floor(Date.now() / 1000) - 4,
              preflight: {
                python_executable: "D:/notebooks/float_dev/backend/.venv/Scripts/python.exe",
                missing_packages: [],
                missing_runtime_components: [],
                hint: null,
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
              installed: true,
              server_running: false,
              model_loaded: false,
              loaded_model: null,
              effective_model_id: "gpt-oss-20b",
              capabilities: { start_stop: true, context_length: true },
              checked_at: Math.floor(Date.now() / 1000) - 3,
            },
          },
        });
      }
      if (url === "/api/llm/provider/logs") {
        return Promise.resolve({
          data: {
            logs: { entries: [], cursor: 0, next_cursor: 0 },
          },
        });
      }
      if (url === "/api/llm/server/models") {
        return Promise.resolve({
          data: {
            reachable: true,
            models: ["server-model"],
            loaded_model: "server-model",
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders agent activity and handles approve action", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "proposed",
            timestamp: now,
            id: "proposal-1",
            chain_id: "msg-1",
          },
        ],
      },
    ];

    const { container } = render(
      <MemoryRouter>
        <AgentConsole
          collapsed={false}
          onToggle={() => {}}
          streamEnabled
          onStreamToggle={() => {}}
          agents={agents}
          onSelectMessage={() => {}}
          backendReady
          onRefreshAgents={() => {}}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: /background/i })).toBeInTheDocument();
    expect(container.querySelector(".right-header-title-row")).toBeInTheDocument();
    expect(container.querySelector(".right-header-controls-scroll")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand background/i })).toHaveTextContent("+");
    const backgroundRegion = screen.getByRole("region", { name: /background/i });
    expect(within(backgroundRegion).getByTitle(/0 active, 0 reflections, 0 queue updates/i)).toBeInTheDocument();
    expect(backgroundRegion.querySelector(".agent-console-pip-row")).not.toBeInTheDocument();
    const runtimePanel = screen.getByRole("heading", { name: /runtime/i }).closest("section");
    expect(runtimePanel?.querySelectorAll(".agent-console-pip")).toHaveLength(4);
    expect(within(runtimePanel).getByTitle(/Runtime lane: Cloud API/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/Runtime model: test-model/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/runtime availability: usable/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/Budget:/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/Retrieval: idle/i)).toBeInTheDocument();
    expect(within(runtimePanel).queryByTitle(/Background work/i)).not.toBeInTheDocument();
    expect(within(backgroundRegion).queryByRole("button", { name: /add background work/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand background/i }));
    expect(screen.getByRole("button", { name: /collapse background/i })).toHaveTextContent("-");
    expect(within(backgroundRegion).getByRole("button", { name: /add background work/i })).toBeInTheDocument();
    fireEvent.click(within(backgroundRegion).getByRole("button", { name: /add background work/i }));
    const promptBox = within(backgroundRegion).getByLabelText(/scheduled prompt/i);
    expect(promptBox).toBeInTheDocument();
    fireEvent.change(promptBox, { target: { value: "Review the latest background queue" } });
    fireEvent.click(within(backgroundRegion).getByRole("button", { name: /^start$/i }));
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        expect.stringMatching(
          /\/api\/calendar\/events\/background-[^/]+$/i,
        ),
        expect.objectContaining({
          title: "Background: Review the latest background queue",
          description: "Review the latest background queue",
          status: "pending",
          actions: [
            expect.objectContaining({
              kind: "prompt",
              prompt: "Review the latest background queue",
              conversation_mode: "new_chat",
            }),
          ],
        }),
      );
    });
    await waitFor(() => {
      expect(
        axios.post.mock.calls.some(
          ([url, payload]) =>
            /\/api\/calendar\/events\/background-.*\/run\?force=true$/i.test(String(url)) &&
            payload === null,
        ),
      ).toBe(true);
    });
    expect(await within(backgroundRegion).findByText(/Started background agent\./i)).toBeInTheDocument();
    expect(within(backgroundRegion).queryByLabelText(/prompt/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand agent card/i })).toHaveTextContent("+");
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getAllByText(/calendar.lookup/i)[0]).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /accept/i })[0]).toBeInTheDocument();
  });

  it("creates a bounded reflection task from the background panel", async () => {
    const onRefreshAgents = vi.fn();
    axios.post.mockResolvedValue({ data: { status: "ran" } });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        backendReady
        onRefreshAgents={onRefreshAgents}
      />,
    );

    const backgroundRegion = screen.getByRole("region", { name: /background/i });
    fireEvent.click(screen.getByRole("button", { name: /expand background/i }));
    fireEvent.click(
      within(backgroundRegion).getByRole("button", {
        name: /add background work/i,
      }),
    );
    fireEvent.change(within(backgroundRegion).getByLabelText(/^mode$/i), {
      target: { value: "reflect" },
    });
    const questionBox = within(backgroundRegion).getByLabelText(/reflection focus/i);
    fireEvent.change(questionBox, {
      target: { value: "Find one useful follow-up from this thread" },
    });
    fireEvent.click(within(backgroundRegion).getByRole("button", { name: /run reflection/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/reflections/tasks",
        expect.objectContaining({
          question: "Find one useful follow-up from this thread",
          source_thread_id: "sess-123",
          patience: 1,
          run_now: true,
          metadata: { source_mode: "current" },
        }),
      );
    });
    expect(onRefreshAgents).toHaveBeenCalled();
    expect(
      await within(backgroundRegion).findByText(/Reflection started\./i),
    ).toBeInTheDocument();
  });

  it("keeps the background composer open while typing spaces", () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        backendReady
      />,
    );

    const backgroundRegion = screen.getByRole("region", { name: /background/i });
    fireEvent.click(screen.getByRole("button", { name: /expand background/i }));
    fireEvent.click(
      within(backgroundRegion).getByRole("button", {
        name: /add background work/i,
      }),
    );
    const promptBox = within(backgroundRegion).getByLabelText(/scheduled prompt/i);
    fireEvent.change(promptBox, { target: { value: "hello" } });
    fireEvent.keyDown(promptBox, { key: " " });
    fireEvent.change(promptBox, { target: { value: "hello world" } });

    expect(within(backgroundRegion).getByLabelText(/scheduled prompt/i)).toHaveValue("hello world");
  });

  it("nests reflection workers inside the background panel", () => {
    const now = Date.now() / 1000;
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "reflection:task-1",
            label: "reflection worker",
            status: "complete",
            updatedAt: now,
            summary: "Reflection saved: one useful next step",
            provenance: { kind: "background_reflection" },
            events: [
              {
                type: "thought",
                content: "Reflection saved: one useful next step",
                timestamp: now,
              },
            ],
          },
        ]}
        backendReady
      />,
    );

    const backgroundRegion = screen.getByRole("region", { name: /background/i });
    fireEvent.click(screen.getByRole("button", { name: /expand background/i }));

    expect(within(backgroundRegion).getByText(/reflection worker/i)).toBeInTheDocument();
    expect(
      within(backgroundRegion).getAllByText(/one useful next step/i).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: /reflection worker/i })).not.toBeInTheDocument();
  });

  it("renders chat grouping rail outside tool activity rows", () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "tool-agent-1",
        label: "remember",
        status: "complete",
        updatedAt: now,
        session_id: "sess-chat-rail",
        events: [
          {
            type: "tool",
            name: "remember",
            status: "invoked",
            result: { status: "ok" },
            timestamp: now,
            id: "tool-rail-1",
            chain_id: "msg-rail-1",
            message_id: "msg-rail-1",
            session_id: "sess-chat-rail",
          },
        ],
      },
      {
        id: "tool-agent-2",
        label: "tool_help",
        status: "complete",
        updatedAt: now - 1,
        session_id: "sess-chat-rail",
        events: [
          {
            type: "tool",
            name: "tool_help",
            status: "invoked",
            result: { status: "ok" },
            timestamp: now - 1,
            id: "tool-rail-2",
            chain_id: "msg-rail-2",
            message_id: "msg-rail-2",
            session_id: "sess-chat-rail",
          },
        ],
      },
    ];
    const { container } = renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        backendReady
      />,
    );

    expect(container.querySelector(".agent-tool-chat-group > .agent-chat-group-line")).not.toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /expand agent card/i })[0]);
    expect(container.querySelector(".agent-activity .agent-chat-group-line")).toBeNull();
  });

  it("stops stream progress for terminal and unresolved conversation responses", () => {
    const now = Date.now() / 1000;
    const streamEvent = (id, status, offset) => ({
      type: "stream",
      id: `stream-${id}`,
      status,
      timestamp: now + offset,
      chain_id: id,
      message_id: id,
    });
    const { container } = renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "agent-streams",
            label: "response activity",
            status: "active",
            updatedAt: now,
            events: [
              streamEvent("msg-active", "active", 0),
              streamEvent("msg-error", "error", 1),
              streamEvent("msg-timeout", "timeout", 2),
              streamEvent("msg-unresolved", "active", 3),
              streamEvent("msg-partial", "active", 4),
            ],
          },
        ]}
        backendReady
      />,
      {
        stateOverrides: {
          conversation: [
            { id: "msg-active", role: "ai", metadata: { status: "streaming" } },
            { id: "msg-error", role: "ai", metadata: {} },
            { id: "msg-timeout", role: "ai", metadata: {} },
            {
              id: "msg-unresolved",
              role: "ai",
              metadata: { unresolved_tool_loop: true },
            },
            { id: "msg-partial", role: "ai", metadata: { status: "partial" } },
          ],
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    screen
      .getAllByRole("button", { name: /expand activity details/i })
      .forEach((button) => fireEvent.click(button));

    expect(screen.getByText("response ended with an error")).toBeInTheDocument();
    expect(screen.getByText("response timed out")).toBeInTheDocument();
    expect(screen.getByText("tool follow-up stopped")).toBeInTheDocument();
    expect(screen.getByText("partial response")).toBeInTheDocument();
    expect(container.querySelectorAll('[role="status"]')).toHaveLength(4);
    expect(
      screen.getAllByRole("progressbar", { name: /streaming response/i }),
    ).toHaveLength(1);
  });

  it("surfaces sync reviews in the console and approves them from there", async () => {
    const now = Date.now() / 1000;
    const onRefreshAgents = vi.fn().mockResolvedValue(undefined);

    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/reviews/review-1/approve") {
        return Promise.resolve({ data: { status: "approved" } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        syncReviews={{
          pending: [
            {
              id: "review-1",
              status: "pending",
              source_label: "Pear",
              created_at: now,
              requested_section_labels: ["Knowledge", "Files"],
            },
          ],
          recent: [
            {
              id: "review-2",
              status: "approved",
              source_label: "Desk",
              updated_at: now - 60,
              requested_section_labels: ["Knowledge"],
            },
          ],
        }}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={onRefreshAgents}
      />,
    );

    expect(await screen.findByRole("heading", { name: /sync inbox/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand sync inbox/i })).toHaveTextContent("+");
    fireEvent.click(screen.getByRole("button", { name: /expand sync inbox/i }));
    expect(screen.getByText("Pear", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/Sections: Knowledge \+ Files/i)).toBeInTheDocument();
    expect(screen.getByText(/recent decisions/i)).toBeInTheDocument();
    expect(screen.getByText("Desk", { selector: "strong" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /approve sync from pear/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/sync/reviews/review-1/approve", {
        note: "",
      });
    });
    await waitFor(() => {
      expect(onRefreshAgents).toHaveBeenCalled();
    });
    expect(await screen.findByText(/Approved sync from Pear\./i)).toBeInTheDocument();
  });

  it("keeps sync reviews collapsed by default during active runs and lets the user expand them", async () => {
    const now = Date.now() / 1000;

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "agent-1",
            label: "browser-agent",
            status: "active",
            updatedAt: now,
            events: [],
          },
        ]}
        syncReviews={{
          pending: [
            {
              id: "review-1",
              status: "pending",
              source_label: "Pear",
              created_at: now,
              requested_section_labels: ["Knowledge"],
            },
          ],
          recent: [],
        }}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /sync inbox/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand sync inbox/i })).toHaveTextContent("+");
    expect(screen.queryByText("Pear", { selector: "strong" })).not.toBeInTheDocument();

    const expandButton = screen.getByRole("button", { name: /expand sync inbox/i });
    expect(expandButton).toHaveTextContent("+");

    fireEvent.click(expandButton);

    expect(await screen.findByText("Pear", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /collapse sync inbox/i })).toHaveTextContent("-");
  });

  it("lets the user hide and restore the sync inbox", async () => {
    const now = Date.now() / 1000;

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        syncReviews={{
          pending: [],
          recent: [
            {
              id: "review-2",
              status: "approved",
              source_label: "Desk",
              updated_at: now - 60,
              requested_section_labels: ["Knowledge"],
            },
          ],
        }}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /sync inbox/i })).toBeInTheDocument();
    const syncInboxRegion = screen.getByRole("region", { name: /sync inbox/i });
    expect(within(syncInboxRegion).queryByText(/recent decisions/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand sync inbox/i }));
    expect(within(syncInboxRegion).getByText(/recent decisions/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /hide sync inbox/i }));

    expect(screen.queryByRole("heading", { name: /sync inbox/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/recent decisions/i)).not.toBeInTheDocument();
    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden console cards/i,
    });
    expect(showHiddenButton).toHaveTextContent("hidden (1)");

    fireEvent.click(showHiddenButton);

    expect(await screen.findByRole("heading", { name: /sync inbox/i })).toBeInTheDocument();
    expect(screen.getByText(/recent decisions/i)).toBeInTheDocument();
  });

  it("resets session-scoped console expansion state when the session changes", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-reset",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "thought",
            content: "Still working",
            timestamp: now,
          },
        ],
      },
    ];

    const syncReviews = {
      pending: [],
      recent: [
        {
          id: "review-reset",
          status: "approved",
          source_label: "Desk",
          updated_at: now - 60,
          requested_section_labels: ["Knowledge"],
        },
      ],
    };

    const view = renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        syncReviews={syncReviews}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          sessionId: "sess-a",
        },
      },
    );

    fireEvent.click(await screen.findByRole("button", { name: /expand sync inbox/i }));
    expect(screen.getByText(/recent decisions/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getByRole("button", { name: /compact agent card/i })).toBeInTheDocument();

    view.rerender(
      <MemoryRouter>
        <GlobalContext.Provider
          value={{
            state: { ...baseGlobalState, sessionId: "sess-b" },
            setState: vi.fn(),
          }}
        >
          <AgentConsole
            collapsed={false}
            onToggle={() => {}}
            streamEnabled
            onStreamToggle={() => {}}
            agents={agents}
            syncReviews={syncReviews}
            onSelectMessage={() => {}}
            backendReady
            onRefreshAgents={() => {}}
          />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(screen.queryByText(/recent decisions/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand sync inbox/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand agent card/i })).toBeInTheDocument();
  });

  it("uses symbol toggles for runtime details", async () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "gpt-oss-20b",
          transformerModel: "gpt-oss-20b",
        },
      },
    );

    const collapseButton = await screen.findByRole("button", {
      name: /expand runtime details/i,
    });
    expect(collapseButton).toHaveTextContent("+");

    fireEvent.click(collapseButton);

    expect(screen.getByRole("button", { name: /collapse runtime details/i })).toHaveTextContent("-");
  });

  it("labels server runtime contextually and shows conversation budget", async () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "server",
          serverUrl: "http://float-box:8000/v1",
          transformerModel: "server-model",
          maxContextLength: 4096,
          conversation: [
            { role: "user", text: "Please summarize this branch." },
            {
              role: "system",
              text: "Compacted earlier work.",
              metadata: {
                conversation_compaction: {
                  prior_compaction_summaries_carried: 1,
                  total_messages: 48,
                },
              },
            },
          ],
        },
      },
    );

    const runtimePanel = screen.getByRole("heading", { name: /runtime/i }).closest("section");
    expect(within(runtimePanel).getAllByText("Server/LAN").length).toBeGreaterThan(0);
    expect(
      within(runtimePanel).getByTitle(/Runtime endpoint: http:\/\/float-box:8000\/v1/i),
    ).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/Runtime model: server-model/i)).toBeInTheDocument();
    expect(
      await within(runtimePanel).findByTitle(/runtime availability: usable/i),
    ).toBeInTheDocument();
    expect(within(runtimePanel).getByTitle(/Budget:/i)).toBeInTheDocument();
    expect(within(runtimePanel).queryByTitle(/API model:/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand runtime details/i }));

    expect(await within(runtimePanel).findByText(/context budget/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByText("2")).toBeInTheDocument();
  });

  it("shows RAG retrieval progress in the runtime card", async () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "api",
          apiModel: "test-model",
          transformerModel: "test-transformer",
        },
      },
    );

    const runtimePanel = screen.getByRole("heading", { name: /runtime/i }).closest("section");

    act(() => {
      window.dispatchEvent(
        new CustomEvent("float:runtime-rag-operation", {
          detail: {
            title: "Retrieving chat context",
            body: "test RAG test",
            category: "operation_progress",
            data: {
              operation_id: "rag-query:knowledge:req-1",
              kind: "rag_query",
              status: "running",
              phase_label: "Searching saved context",
              phase_index: 1,
              phase_count: 3,
              started_at: "2026-04-19T00:00:00Z",
              detail: "Searching memory and knowledge before the model call.",
              counts: { requested_top_k: 6, clip_top_k: 3 },
            },
          },
        }),
      );
    });

    expect(within(runtimePanel).getByTitle(/Retrieval: retrieving/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand runtime details/i }));

    expect(await within(runtimePanel).findByText(/Searching saved context/i)).toBeInTheDocument();
    expect(within(runtimePanel).getByText(/top 6 text \+ 3 vision/i)).toBeInTheDocument();
    expect(
      within(runtimePanel).getByText(/Searching memory and knowledge before the model call\./i),
    ).toBeInTheDocument();
  });

  it("flags provider runtimes that are running outside Float", async () => {
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
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
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: true,
                load_unload: true,
                context_length: true,
              },
              checked_at: Math.floor(Date.now() / 1000) - 3,
            },
          },
        });
      }
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
              effective_model_id: "gpt-oss-20b",
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: true,
                load_unload: true,
                context_length: true,
              },
              checked_at: Math.floor(Date.now() / 1000) - 3,
            },
          },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    await expandRuntimeDetails();

    expect(
      await screen.findByTitle(/runtime availability: usable/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/switch this lane to External HTTP only before using start, stop, load, or unload here\./i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Explain provider runtime state"));
    const inspector = screen.getByRole("dialog", {
      name: "Why this runtime state is shown",
    });
    expect(within(inspector).getByText("Owner")).toBeInTheDocument();
    expect(within(inspector).getByText("outside Float")).toBeInTheDocument();
  });

  it("does not auto-scroll the console when the user has moved away from the bottom", async () => {
    const now = Date.now() / 1000;
    const setState = vi.fn();
    const state = { ...baseGlobalState };
    const scrollToSpy = vi.fn();

    const { container, rerender } = render(
      <MemoryRouter>
        <GlobalContext.Provider value={{ state, setState }}>
          <AgentConsole
            collapsed={false}
            onToggle={() => {}}
            streamEnabled
            onStreamToggle={() => {}}
            agents={[
              {
                id: "agent-1",
                label: "browser-agent",
                status: "active",
                updatedAt: now,
                events: [{ type: "thought", content: "one", timestamp: now }],
              },
            ]}
            onSelectMessage={() => {}}
            backendReady
            onRefreshAgents={() => {}}
          />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    const body = container.querySelector(".agent-console-body");
    if (!body) {
      throw new Error("agent console body not found");
    }
    Object.defineProperty(body, "clientHeight", { configurable: true, value: 300 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(body, "scrollTo", { configurable: true, value: scrollToSpy });

    await act(async () => {
      body.scrollTop = 100;
      fireEvent.scroll(body);
    });

    scrollToSpy.mockClear();

    await act(async () => {
      rerender(
        <MemoryRouter>
          <GlobalContext.Provider value={{ state, setState }}>
            <AgentConsole
              collapsed={false}
              onToggle={() => {}}
              streamEnabled
              onStreamToggle={() => {}}
              agents={[
                {
                  id: "agent-1",
                  label: "browser-agent",
                  status: "active",
                  updatedAt: now + 1,
                  events: [
                    { type: "thought", content: "one", timestamp: now },
                    { type: "thought", content: "two", timestamp: now + 1 },
                  ],
                },
              ]}
              onSelectMessage={() => {}}
              backendReady
              onRefreshAgents={() => {}}
            />
          </GlobalContext.Provider>
        </MemoryRouter>,
      );
    });

    expect(scrollToSpy).not.toHaveBeenCalled();
  });

  it("integrates tracked writes into matching tool rows and supports revert controls", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "writer",
        status: "active",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "write_file",
            args: { path: "workspace/notes.md" },
            status: "invoked",
            timestamp: now,
            id: "proposal-1",
            chain_id: "msg-1234",
            message_id: "msg-1234",
            session_id: "sess-123",
          },
          {
            type: "tool",
            name: "search_web",
            args: { query: "related note context" },
            status: "invoked",
            timestamp: now + 1,
            id: "proposal-2",
            chain_id: "msg-1234",
            message_id: "msg-1234",
            session_id: "sess-123",
          },
        ],
      },
    ];
    const actions = [
      {
        id: "action-1",
        kind: "tool",
        name: "write_file",
        summary: "write_file applied: notes.md",
        status: "invoked",
        created_at_ts: now,
        conversation_id: "sess-123",
        conversation_label: "project alpha",
        response_id: "msg-1234",
        response_label: "draft reply",
        item_count: 1,
        revertible: true,
      },
    ];
    const onRefreshAgents = vi.fn();

    axios.get.mockImplementation((url) => {
      if (url === "/api/actions/action-1") {
        return Promise.resolve({
          data: {
            action: {
              id: "action-1",
              items: [
                {
                  id: "files:workspace/notes.md",
                  label: "workspace/notes.md",
                  operation: "update",
                  section: "files",
                  diff: { unified: "@@ -1 +1 @@\n-old\n+new" },
                },
              ],
            },
          },
        });
      }
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              provider: "lmstudio",
              installed: true,
              server_running: false,
              model_loaded: false,
              loaded_model: null,
              capabilities: { start_stop: true, context_length: true },
            },
          },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b"],
            runtime: { loaded_model: null },
          },
        });
      }
      if (url === "/api/llm/provider/logs") {
        return Promise.resolve({
          data: {
            logs: { entries: [], cursor: 0, next_cursor: 0 },
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/actions/revert") {
        return Promise.resolve({
          data: {
            status: "reverted",
            action: { summary: "Reverted draft reply." },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        actions={actions}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={onRefreshAgents}
      />,
    );

    expect(await screen.findByText(/writer/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getAllByText(/write_file/i)[0]).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /open work history \(1\)/i })).toHaveLength(1);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /open work history \(1\)/i }));
    });

    await waitFor(() => {
      expect(
        axios.get.mock.calls.some(([url]) => url === "/api/actions/action-1"),
      ).toBe(true);
    });
    const historyDialog = await screen.findByRole("dialog", { name: /work history/i });
    expect(within(historyDialog).getByText(/draft reply/i)).toBeInTheDocument();
    expect(within(historyDialog).getByText(/workspace\/notes\.md/i, { selector: "strong" })).toBeInTheDocument();
    expect(within(historyDialog).getByText(/-old/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /revert set/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/actions/revert", {
        response_id: "msg-1234",
        conversation_id: "sess-123",
        force: false,
      });
    });
    await waitFor(() => {
      expect(onRefreshAgents).toHaveBeenCalled();
    });
    expect(await screen.findByText(/reverted draft reply/i)).toBeInTheDocument();
  });

  it("shows linked undo references and partial counts in write history", () => {
    const syncTs = Date.parse("2026-03-24T23:38:00Z") / 1000;
    const revertTs = Date.parse("2026-03-25T20:35:00Z") / 1000;
    const actions = [
      {
        id: "action-sync",
        kind: "sync",
        name: "sync_ingest",
        summary: "Sync ingest from Pear",
        status: "applied",
        created_at_ts: syncTs,
        item_count: 54,
        revertible: true,
        reverted_at: revertTs,
        reverted_by_action_id: "action-revert",
      },
      {
        id: "action-revert",
        kind: "revert",
        name: "revert_actions",
        summary: "Reverted Sync ingest from Pear",
        status: "applied",
        created_at_ts: revertTs,
        item_count: 15,
        revertible: true,
        target_action_ids: ["action-sync"],
      },
    ];

    render(
      <MemoryRouter>
        <ActionHistoryPanel actions={actions} backendReady={false} onRefresh={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByText("partly undone")).toBeInTheDocument();
    expect(screen.getByText(/Undo target: .*Sync ingest from Pear/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Restored 15 of 54 tracked items\. 39 already matched the earlier state\./i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Later undo: .*Reverted Sync ingest from Pear/i)).toBeInTheDocument();
    expect(screen.getByText(/That undo restored 15 of 54 tracked items\./i)).toBeInTheDocument();
  });

  it("renders deployment metadata without offering a content diff", () => {
    render(
      <MemoryRouter>
        <ActionHistoryPanel
          actions={[
            {
              id: "deployment-event:event-1",
              kind: "deployment",
              name: "software_install",
              summary: "Installed Float 0.1.0a1 // b-test",
              status: "completed",
              created_at_ts: Date.parse("2026-07-16T12:00:00Z") / 1000,
              item_count: 0,
              revertible: false,
              metadata_only: true,
            },
          ]}
          backendReady
          onRefresh={() => {}}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText(/Installed Float 0\.1\.0a1 \/\/ b-test/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show diff/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /revert action/i })).toBeDisabled();
  });

  it("lets individual write items minimize, hide, and restore inside write history", () => {
    const ts = Date.parse("2026-03-24T23:38:00Z") / 1000;
    const actions = [
      {
        id: "action-1",
        kind: "write",
        name: "write_file",
        summary: "Draft reply",
        status: "applied",
        created_at_ts: ts,
        item_count: 1,
        revertible: true,
        response_id: "response-1",
        response_label: "response 1",
        conversation_id: "sess-123",
        conversation_label: "Current chat",
      },
    ];

    render(
      <MemoryRouter>
        <ActionHistoryPanel actions={actions} backendReady={false} onRefresh={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /minimize draft reply/i })).toHaveTextContent("-");

    fireEvent.click(screen.getByRole("button", { name: /minimize draft reply/i }));
    expect(screen.getByRole("button", { name: /expand draft reply/i })).toHaveTextContent("+");

    fireEvent.click(screen.getByRole("button", { name: /hide draft reply/i }));
    expect(screen.queryByText(/draft reply/i)).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden write items/i,
    });
    expect(showHiddenButton).toHaveTextContent("hidden (1)");

    fireEvent.click(showHiddenButton);
    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();
  });

  it("auto-continues once after accepting a tool (non-auto mode)", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "proposed",
            timestamp: now,
            id: "proposal-1",
            chain_id: "msg-1",
            message_id: "msg-1",
            session_id: "sess-123",
          },
        ],
      },
    ];

    axios.post.mockImplementation((url) => {
      if (url === "/api/tools/decision") {
        return Promise.resolve({
          data: {
            status: "invoked",
            result: { status: "invoked", ok: true, message: null, data: { ok: true } },
          },
        });
      }
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: { message: "continued", metadata: {} },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <MemoryRouter>
        <AgentConsole
          collapsed={false}
          onToggle={() => {}}
          streamEnabled
          onStreamToggle={() => {}}
          agents={agents}
          onSelectMessage={() => {}}
          backendReady
          onRefreshAgents={() => {}}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    fireEvent.click(screen.getByText("Accept", { selector: "button" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/tools/decision",
        expect.objectContaining({ request_id: "proposal-1", decision: "accept" }),
      );
    });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/chat/continue",
        expect.objectContaining({
          session_id: "sess-123",
          message_id: "msg-1",
        }),
      );
    });
  });

  it("accepts and continues a pending tool batch from the console", async () => {
    const now = Date.now() / 1000;
    const tools = [
      {
        id: "proposal-1",
        name: "search_web",
        args: { query: "float privacy first" },
        status: "proposed",
      },
      {
        id: "proposal-2",
        name: "recall",
        args: { query: "user profile preferences" },
        status: "proposed",
      },
    ];
    const agents = [
      {
        id: "agent-batch",
        label: "search_web + recall",
        status: "pending",
        updatedAt: now,
        events: tools.map((tool, index) => ({
          ...tool,
          type: "tool",
          timestamp: now + index,
          chain_id: "msg-batch-1",
          message_id: "msg-batch-1",
          session_id: "sess-123",
        })),
      },
    ];

    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/tools/decision") {
        return Promise.resolve({
          data: {
            status: "invoked",
            result: {
              status: "invoked",
              ok: true,
              message: `Ran ${payload.name}.`,
              data: { name: payload.name },
            },
          },
        });
      }
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: { message: "continued", metadata: {} },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          conversation: [
            {
              id: "msg-batch-1",
              role: "ai",
              text: "Need tools.",
              metadata: { tool_response_pending: true },
              tools,
            },
          ],
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    fireEvent.click(
      await screen.findByRole("button", { name: /continue the assistant response/i }),
    );

    await waitFor(() => {
      const decisions = axios.post.mock.calls.filter(([url]) => url === "/api/tools/decision");
      expect(decisions).toHaveLength(2);
    });
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/chat/continue",
        expect.objectContaining({
          session_id: "sess-123",
          message_id: "msg-batch-1",
          tools: [
            expect.objectContaining({ id: "proposal-1", name: "search_web", status: "invoked" }),
            expect.objectContaining({ id: "proposal-2", name: "recall", status: "invoked" }),
          ],
        }),
      );
    });
  });

  it("handles tool review notification actions through the console controls", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "proposed",
            timestamp: now,
            id: "proposal-1",
            chain_id: "msg-1",
            message_id: "msg-1",
            session_id: "sess-123",
          },
        ],
      },
    ];

    axios.post.mockImplementation((url) => {
      if (url === "/api/tools/decision") {
        return Promise.resolve({
          data: {
            status: "invoked",
            result: { status: "invoked", ok: true, data: { ok: true } },
          },
        });
      }
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: { message: "continued", metadata: {} },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <MemoryRouter>
        <AgentConsole
          collapsed={false}
          onToggle={() => {}}
          streamEnabled
          onStreamToggle={() => {}}
          agents={agents}
          onSelectMessage={() => {}}
          backendReady
          onRefreshAgents={() => {}}
        />
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: /expand agent card/i });

    fireEvent(
      window,
      new CustomEvent(TOOL_REVIEW_ACTION_EVENT, {
        detail: {
          action: "accept",
          scope: "selected",
          selectedToolId: "stale-proposal",
          toolIds: ["proposal-1"],
          chainId: "msg-1",
        },
      }),
    );
    expect(
      axios.post.mock.calls.some(([url]) => url === "/api/tools/decision"),
    ).toBe(false);

    fireEvent(
      window,
      new CustomEvent(TOOL_REVIEW_ACTION_EVENT, {
        detail: {
          action: "accept",
          scope: "selected",
          selectedToolId: "proposal-1",
          toolIds: ["proposal-1"],
        },
      }),
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/tools/decision",
        expect.objectContaining({ request_id: "proposal-1", decision: "accept" }),
      );
    });
  });

  it("clears stale pending metadata after a continuation resolves", async () => {
    const currentState = {
      ...baseGlobalState,
      conversation: [
        {
          role: "user",
          id: "msg-1:user",
          text: "Use a tool.",
        },
        {
          role: "ai",
          id: "msg-1",
          text: "Requested tool tool_info. Awaiting approval.",
          metadata: {
            tool_response_pending: true,
          },
          tools: [
            {
              id: "proposal-1",
              name: "tool_info",
              status: "error",
              args: { tool: "read_file", include_schema: true },
              result: {
                status: "error",
                ok: false,
                message: "Missing required argument(s): tool_name",
              },
            },
          ],
        },
      ],
      history: [
        { role: "user", text: "Use a tool." },
        { role: "ai", text: "Requested tool tool_info. Awaiting approval." },
      ],
    };
    let nextState = currentState;
    const setState = vi.fn((update) => {
      nextState = typeof update === "function" ? update(nextState) : update;
    });

    axios.post.mockImplementation((url) => {
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: {
            message: "continued",
            metadata: {},
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      { stateOverrides: currentState, setState },
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/chat/continue",
        expect.objectContaining({
          session_id: "sess-123",
          message_id: "msg-1",
          tools: [
            expect.objectContaining({
              id: "proposal-1",
              status: "error",
            }),
          ],
        }),
      );
    });

    await waitFor(() => {
      expect(nextState.conversation[1].metadata.tool_response_pending).toBe(false);
      expect(nextState.conversation[1].metadata.tool_continued).toBe(true);
      expect(nextState.conversation[1].text).toBe("continued");
    });
  });

  it("keeps synthetic fallback tools denyable while manual accept stays disabled", async () => {
    const now = Date.now() / 1000;
    const setState = vi.fn();
    const syntheticTool = {
      name: "remember",
      args: { value: "project note" },
      status: "proposed",
      synthetic: true,
      synthetic_id: "command-fallback:msg-synth-1:remember",
      manual_fill_required: true,
    };

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "agent-synth",
            label: "command-fallback",
            status: "pending",
            updatedAt: now,
            events: [
              {
                type: "tool",
                name: "remember",
                args: { value: "project note" },
                status: "proposed",
                timestamp: now,
                chain_id: "msg-synth-1",
                message_id: "msg-synth-1",
                session_id: "sess-123",
                synthetic: true,
                synthetic_id: "command-fallback:msg-synth-1:remember",
                manual_fill_required: true,
              },
            ],
          },
        ]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          conversation: [
            {
              id: "msg-synth-1",
              role: "ai",
              text: "Need confirmation.",
              tools: [syntheticTool],
            },
          ],
        },
        setState,
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    const acceptButton = await screen.findByText("Accept", { selector: "button" });
    const denyButton = screen.getByText("Deny", { selector: "button" });

    expect(acceptButton).toBeDisabled();
    expect(denyButton).toBeEnabled();

    fireEvent.click(denyButton);

    await waitFor(() => {
      expect(setState).toHaveBeenCalled();
    });
    expect(
      axios.post.mock.calls.some(([url]) => url === "/api/tools/decision"),
    ).toBe(false);

    const updater = setState.mock.calls.at(-1)?.[0];
    expect(typeof updater).toBe("function");

    const nextState = updater({
      ...baseGlobalState,
      conversation: [
        {
          id: "msg-synth-1",
          role: "ai",
          text: "Need confirmation.",
          tools: [syntheticTool],
        },
      ],
    });

    expect(nextState.conversation[0].tools[0]).toMatchObject({
      status: "denied",
      result: {
        status: "denied",
        message: "Dismissed by user.",
      },
    });
  });

  it("auto-resolves client camera tools in high approval mode and continues the batch", async () => {
    const now = Date.now() / 1000;
    const stopTrack = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tagName, options) => {
        if (String(tagName).toLowerCase() === "video") {
          return {
            playsInline: false,
            muted: false,
            srcObject: null,
            readyState: 2,
            videoWidth: 640,
            videoHeight: 480,
            play: vi.fn().mockResolvedValue(undefined),
            onloadedmetadata: null,
          };
        }
        if (String(tagName).toLowerCase() === "canvas") {
          return {
            width: 0,
            height: 0,
            getContext: vi.fn().mockReturnValue({ drawImage: vi.fn() }),
            toBlob: (callback) =>
              callback(new Blob(["camera"], { type: "image/png" })),
          };
        }
        return originalCreateElement(tagName, options);
      });
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: stopTrack }],
        }),
      },
    });

    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/captures/upload") {
        return Promise.resolve({
          data: {
            capture_id: "capture-1",
            source: "camera",
            transient: true,
            url: "/api/captures/capture-1/content",
          },
        });
      }
      if (url === "/api/tools/client-resolve") {
        return Promise.resolve({
          data: {
            status: "invoked",
            result: {
              status: "invoked",
              ok: true,
              message: "Captured camera image.",
              data: { capture_id: "capture-1" },
            },
          },
        });
      }
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: {
            message: "continued",
            metadata: { tool_continue_signature: "sig-1" },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "agent-camera",
            label: "camera-agent",
            status: "pending",
            updatedAt: now,
            events: [
              {
                type: "tool",
                name: "camera.capture",
                args: {},
                status: "proposed",
                timestamp: now,
                id: "proposal-camera-1",
                chain_id: "msg-camera-1",
                message_id: "msg-camera-1",
                session_id: "sess-123",
              },
            ],
          },
        ]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          approvalLevel: "high",
          conversation: [
            {
              id: "msg-camera-1",
              role: "ai",
              text: "Need a camera frame.",
              metadata: { tool_response_pending: true },
              tools: [
                {
                  id: "proposal-camera-1",
                  name: "camera.capture",
                  args: {},
                  status: "proposed",
                },
              ],
            },
          ],
        },
      },
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/tools/client-resolve",
        expect.objectContaining({
          request_id: "proposal-camera-1",
          status: "invoked",
        }),
      );
    });
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/chat/continue",
        expect.objectContaining({
          session_id: "sess-123",
          message_id: "msg-camera-1",
          tools: [
            expect.objectContaining({
              id: "proposal-camera-1",
              name: "camera.capture",
              status: "invoked",
            }),
          ],
        }),
      );
    });
    expect(stopTrack).toHaveBeenCalled();
    createElementSpy.mockRestore();
  });

  it("shows a continue button for resolved tool batches in the console", async () => {
    const now = Date.now() / 1000;
    const resolvedTool = {
      type: "tool",
      name: "calendar.lookup",
      args: { query: "today" },
      status: "invoked",
      result: { status: "invoked", ok: true, message: null, data: { ok: true } },
      timestamp: now,
      id: "proposal-1",
      chain_id: "msg-1",
      message_id: "msg-1",
      session_id: "sess-123",
    };
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [resolvedTool],
      },
    ];

    axios.post.mockImplementation((url) => {
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: { message: "continued", metadata: {} },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          conversation: [
            {
              id: "msg-1",
              role: "ai",
              text: "Requested tool.",
              tools: [
                {
                  id: "proposal-1",
                  name: "calendar.lookup",
                  args: { query: "today" },
                  status: "invoked",
                  result: { status: "invoked", ok: true, message: null, data: { ok: true } },
                },
              ],
            },
          ],
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    fireEvent.click(await screen.findByRole("button", { name: /expand activity details/i }));
    fireEvent.click(await screen.findByText("Continue", { selector: "button" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/chat/continue",
        expect.objectContaining({
          session_id: "sess-123",
          message_id: "msg-1",
        }),
      );
    });
  });

  it("expands a collapsed tool row when the row body is clicked", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-row-click",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "invoked",
            result: { status: "ok" },
            timestamp: now,
            id: "proposal-row-click",
            chain_id: "msg-row-click",
            message_id: "msg-row-click",
            session_id: "sess-123",
          },
        ],
      },
    ];

    const { container } = renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        backendReady
      />,
    );

    const expandAgentButtons = screen.queryAllByRole("button", {
      name: /expand agent card/i,
    });
    if (expandAgentButtons[0]) {
      fireEvent.click(expandAgentButtons[0]);
    }

    const toolRow = container.querySelector(
      '.agent-activity.agent-activity-tool[data-tool-id="proposal-row-click"]',
    );
    expect(toolRow).not.toBeNull();
    expect(toolRow.querySelector(".agent-activity-preview")).not.toBeNull();
    expect(toolRow.querySelector(".agent-activity-body")).toBeNull();

    fireEvent.click(toolRow);

    await waitFor(() => {
      expect(toolRow.querySelector(".agent-activity-body")).not.toBeNull();
    });
    expect(within(toolRow).getByText(/calendar\.lookup/i)).toBeInTheDocument();
  });

  it("expands a focused tool row when opened from a notification", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-focus-open",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "invoked",
            result: { status: "ok" },
            timestamp: now,
            id: "proposal-focus-open",
            chain_id: "msg-focus-open",
            message_id: "msg-focus-open",
            session_id: "sess-123",
          },
        ],
      },
    ];

    const { container } = renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={agents}
        backendReady
        focus={{
          toolId: "proposal-focus-open",
          chainId: "msg-focus-open",
          ts: now,
        }}
      />,
    );

    const expandAgentButtons = screen.queryAllByRole("button", {
      name: /expand agent card/i,
    });
    if (expandAgentButtons[0]) {
      fireEvent.click(expandAgentButtons[0]);
    }

    const toolRow = container.querySelector(
      '.agent-activity.agent-activity-tool[data-tool-id="proposal-focus-open"]',
    );
    expect(toolRow).not.toBeNull();
    expect(toolRow.querySelector(".agent-activity-body")).not.toBeNull();
    expect(within(toolRow).getByText(/calendar\.lookup/i)).toBeInTheDocument();
  });

  it("opens a browser session popup from computer tool results and refreshes through computer.observe", async () => {
    const now = Date.now() / 1000;

    axios.post.mockImplementation((url) => {
      if (url === "/api/tools/invoke") {
        return Promise.resolve({
          data: {
            result: {
              status: "invoked",
              ok: true,
              data: {
                summary: "Refreshed browser state",
                session: {
                  id: "browser-session-1",
                  runtime: "browser",
                  width: 1280,
                  height: 720,
                },
                attachment: {
                  url: "/api/captures/capture-2/content",
                  name: "capture-2.png",
                },
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled
        onStreamToggle={() => {}}
        agents={[
          {
            id: "agent-browser",
            label: "browser-agent",
            status: "active",
            updatedAt: now,
            events: [
              {
                type: "tool",
                name: "computer.observe",
                args: { session_id: "browser-session-1" },
                status: "invoked",
                timestamp: now,
                id: "browser-tool-1",
                chain_id: "msg-browser-1",
                message_id: "msg-browser-1",
                session_id: "sess-123",
                result: {
                  status: "invoked",
                  ok: true,
                  data: {
                    summary: "Captured browser state",
                    session: {
                      id: "browser-session-1",
                      runtime: "browser",
                      width: 1280,
                      height: 720,
                    },
                    current_url: "https://example.com",
                    attachment: {
                      url: "/api/captures/capture-1/content",
                      name: "capture-1.png",
                    },
                  },
                },
              },
            ],
          },
        ]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    fireEvent.click(await screen.findByRole("button", { name: /expand activity details/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /expand browser/i }).at(-1));

    const dialog = await screen.findByRole("dialog", {
      name: /browser session controls/i,
    });
    expect(within(dialog).getByDisplayValue("https://example.com")).toBeInTheDocument();
    expect(within(dialog).getByAltText("capture-1.png")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /^refresh$/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/tools/invoke",
        expect.objectContaining({
          name: "computer.observe",
          args: { session_id: "browser-session-1" },
          message_id: "msg-browser-1",
          chain_id: "msg-browser-1",
          session_id: "sess-123",
        }),
      );
    });
  });

  it("opens the task editor and creates a quick task", async () => {
    const selected = new Date("2024-01-01T00:00:00Z");
    const events = [];
    const onRefreshCalendar = vi.fn();

    axios.post.mockResolvedValue({ data: { status: "saved" } });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        isCalendar
        events={events}
        backendReady
        onRefreshCalendar={onRefreshCalendar}
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          userTimezone: "America/New_York",
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /create a task/i }));
    expect(screen.getByText(/task editor/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("America/New_York")).toBeInTheDocument();

    const titleInput = screen.getByPlaceholderText(/follow up on q4 roadmap/i);
    fireEvent.change(titleInput, { target: { value: "My task" } });

    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

    const expectedId = `${slugify("My task")}-${selected.getTime()}`;
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        `/api/calendar/events/${encodeURIComponent(expectedId)}`,
        expect.objectContaining({
          id: expectedId,
          title: "My task",
          timezone: "America/New_York",
          status: "pending",
        }),
      );
    });
    expect(onRefreshCalendar).toHaveBeenCalled();
  });

  it("shows overdue status for past pending tasks and routes task state changes through the review editor", async () => {
    const events = [
      {
        id: "past-1",
        title: "Past task",
        summary: "Past task",
        start_time: 1,
        end_time: 60,
        timezone: "UTC",
        status: "pending",
      },
      {
        id: "done-1",
        title: "Done task",
        summary: "Done task",
        start_time: 1,
        end_time: 60,
        timezone: "UTC",
        status: "acknowledged",
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        isCalendar
        events={events}
        backendReady
        onRefreshCalendar={() => {}}
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByText("Overdue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View" })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Review" }));
    });

    expect(screen.getByText(/task editor/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Past task")).toBeInTheDocument();
  });

  it("shows legacy proposed tasks as scheduled with normalized copy", async () => {
    const events = [
      {
        id: "scheduled-1",
        title: "Scheduled task",
        summary: "Scheduled task",
        start_time: 4102444800,
        end_time: 4102448400,
        timezone: "UTC",
        status: "proposed",
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        isCalendar
        events={events}
        backendReady
        onRefreshCalendar={() => {}}
        onRefreshAgents={() => {}}
      />,
    );

    const scheduledBadge = await screen.findByText("Scheduled");
    expect(scheduledBadge).toHaveAttribute("title", "scheduled");
  });

  it("does not auto-invoke a tool when opening the tool editor", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "tool-agent",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "proposed",
            timestamp: now,
            id: "proposal-1",
            chain_id: "msg-1",
          },
        ],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    axios.get.mockResolvedValue({ data: { tools: [] } });

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    expect(screen.getByText(/tool editor/i)).toBeInTheDocument();
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("supports keyboard resizing and reset on the console resizer", async () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1400 });
    document.documentElement.style.removeProperty("--sidebar-width-right");
    try {
      await act(async () => {
        render(
          <MemoryRouter>
            <AgentConsole
              collapsed={false}
              onToggle={() => {}}
              streamEnabled={false}
              onStreamToggle={() => {}}
              agents={[]}
              onSelectMessage={() => {}}
              backendReady
              onRefreshAgents={() => {}}
            />
          </MemoryRouter>,
        );
      });

      const resizer = screen.getByRole("separator", { name: /resize agent console/i });

      await act(async () => {
        fireEvent.keyDown(resizer, { key: "ArrowLeft" });
      });
      expect(document.documentElement.style.getPropertyValue("--sidebar-width-right")).toBe("240px");

      await act(async () => {
        fireEvent.keyDown(resizer, { key: "ArrowRight", shiftKey: true });
      });
      expect(document.documentElement.style.getPropertyValue("--sidebar-width-right")).toBe("220px");

      await act(async () => {
        fireEvent.keyDown(resizer, { key: "Home" });
      });
      expect(document.documentElement.style.getPropertyValue("--sidebar-width-right")).toBe("");
    } finally {
      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: originalInnerWidth,
      });
    }
  });

  it("renders local provider runtime controls and triggers provider actions", async () => {
    axios.post.mockResolvedValue({ data: { status: "success" } });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    expect(
      await screen.findByText("unavailable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    await expandRuntimeDetails();
    expect(screen.getByRole("button", { name: "start" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "load" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "start" }));
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/llm/provider/start",
        expect.objectContaining({ provider: "lmstudio" }),
      );
    });
  });

  it("renders remote external endpoints with inventory visible but without local process controls", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", model: "lmstudio", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gemma-4-e4b-it", "gpt-oss-20b", "text-embedding-nomic-embed-text-v1.5"],
            runtime: {
              provider: "lmstudio",
              mode: "remote-unmanaged",
              installed: true,
              server_running: true,
              model_loaded: false,
              loaded_model: null,
              effective_model_id: "gemma-4-e4b-it",
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: false,
                load_unload: true,
                context_length: true,
                logs_stream: false,
              },
              checked_at: Math.floor(Date.now() / 1000) - 2,
            },
          },
        });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              provider: "lmstudio",
              mode: "remote-unmanaged",
              installed: true,
              server_running: true,
              model_loaded: false,
              loaded_model: null,
              effective_model_id: "gemma-4-e4b-it",
              base_url: "http://127.0.0.1:1234/v1",
              capabilities: {
                start_stop: false,
                load_unload: true,
                context_length: true,
                logs_stream: false,
              },
              checked_at: Math.floor(Date.now() / 1000) - 2,
            },
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    expect(
      await screen.findByText("usable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    await expandRuntimeDetails();
    expect(screen.getByText("remote endpoint")).toBeInTheDocument();
    expect(screen.getByText(/2 models/i)).toBeInTheDocument();
    const providerSelect = screen.getByTitle(/provider target model/i);
    expect(providerSelect).toHaveDisplayValue("gemma-4-e4b-it");
    expect(screen.getByText(/4B params/i)).toBeInTheDocument();
    fireEvent.change(providerSelect, {
      target: { value: "gpt-oss-20b" },
    });
    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/settings", {
        local_provider_preferred_model: "gpt-oss-20b",
      });
    });
    expect(screen.getByText(/20B params/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set target/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^start$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^stop$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^load$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^unload$/i })).toBeInTheDocument();
    expect(screen.getByLabelText("context")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show logs/i })).not.toBeInTheDocument();
  });

  it("updates provider status label and selected model after refresh", async () => {
    let providerSnapshotCalls = 0;
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", model: "lmstudio", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/models") {
        providerSnapshotCalls += 1;
        return Promise.resolve({
          data: {
            models: ["gemma-4-a", "gemma-4-b"],
            runtime:
              providerSnapshotCalls < 2
                ? {
                    provider: "lmstudio",
                    installed: false,
                    server_running: false,
                    model_loaded: false,
                    loaded_model: "gemma-4-a",
                    effective_model_id: "gemma-4-a",
                    capabilities: { start_stop: true, context_length: true },
                  }
                : {
                    provider: "lmstudio",
                    installed: true,
                    server_running: true,
                    model_loaded: true,
                    loaded_model: "gemma-4-b",
                    effective_model_id: "gemma-4-b",
                    capabilities: { start_stop: true, context_length: true },
                    checked_at: Math.floor(Date.now() / 1000) - 2,
                  },
          },
        });
      }
      if (url === "/api/llm/provider/logs") {
        return Promise.resolve({ data: { logs: { entries: [], cursor: 0, next_cursor: 0 } } });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    expect(
      await screen.findByText("unavailable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    await expandRuntimeDetails();
    expect(await screen.findByDisplayValue("gemma-4-a")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /refresh provider runtime status/i }));
    await waitFor(() => {
      expect(providerSnapshotCalls).toBeGreaterThanOrEqual(2);
    });
    expect(await screen.findByDisplayValue("gemma-4-b")).toBeInTheDocument();
  });

  it("surfaces the last provider action without opening provider logs", async () => {
    const defaultGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...rest) => {
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gpt-oss-20b"],
            runtime: {
              provider: "lmstudio",
              installed: true,
              server_running: true,
              model_loaded: true,
              loaded_model: "gpt-oss-20b",
              effective_model_id: "gpt-oss-20b",
              capabilities: { start_stop: true, context_length: true },
              checked_at: Math.floor(Date.now() / 1000) - 3,
              last_operation: {
                id: "load#7",
                action: "load",
                status: "error",
                model: "gpt-oss-20b",
                started_at: Math.floor(Date.now() / 1000) - 9,
                finished_at: Math.floor(Date.now() / 1000) - 8,
                duration_ms: 842,
                result: {
                  error: "Remote load endpoint unavailable",
                  endpoint: "http://127.0.0.1:1234/v1/responses",
                },
              },
            },
          },
        });
      }
      return defaultGet ? defaultGet(url, ...rest) : Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    expect(
      await screen.findByText("usable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    await expandRuntimeDetails();
    const lastActionDetails = screen
      .getAllByText(/Last action: load#7 failed for gpt-oss-20b/i)
      .find((element) => element.classList.contains("runtime-panel-note"));
    expect(lastActionDetails).toBeInTheDocument();
    expect(lastActionDetails).toHaveAttribute(
      "title",
      expect.stringContaining("endpoint http://127.0.0.1:1234/v1/responses"),
    );
  });

  it("polls provider runtime less often and only loads provider logs on demand", async () => {
    const intervalDelays = [];
    const providerLogCalls = [];
    let providerModelCalls = 0;
    vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
      intervalDelays.push(delay);
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => {});
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", model: "lmstudio", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/models") {
        providerModelCalls += 1;
        return Promise.resolve({
          data: {
            models: ["gemma4:e4b"],
            runtime: {
              provider: "lmstudio",
              installed: true,
              server_running: true,
              model_loaded: true,
              loaded_model: "gemma4:e4b",
              effective_model_id: "gemma4:e4b",
              capabilities: { start_stop: true, context_length: true },
              checked_at: Math.floor(Date.now() / 1000) - 5,
            },
          },
        });
      }
      if (url === "/api/llm/provider/status") {
        return Promise.resolve({
          data: {
            runtime: {
              provider: "lmstudio",
              installed: true,
              server_running: true,
              model_loaded: true,
              loaded_model: "gemma4:e4b",
              effective_model_id: "gemma4:e4b",
              capabilities: { start_stop: true, context_length: true },
              checked_at: Math.floor(Date.now() / 1000) - 5,
            },
          },
        });
      }
      if (url === "/api/llm/provider/logs") {
        providerLogCalls.push(url);
        return Promise.resolve({
          data: { logs: { entries: ["provider log line"], cursor: 0, next_cursor: 1 } },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "lmstudio",
          transformerModel: "lmstudio",
        },
      },
    );

    expect(
      await screen.findByText("usable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    expect(intervalDelays).toContain(60000);
    expect(providerLogCalls).toHaveLength(0);
    expect(providerModelCalls).toBe(1);
    expect(screen.queryByText(/Inventory checked/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Provider inventory freshness")).toBeInTheDocument();
    await expandRuntimeDetails();

    fireEvent.click(screen.getByRole("button", { name: /show logs/i }));

    await waitFor(() => {
      expect(providerLogCalls).toHaveLength(1);
    });
    expect(providerModelCalls).toBe(1);
  });

  it("renders long ollama model ids in the provider runtime controls", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", model: "ollama", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({
          data: {
            models: ["gemma-4-E2B-it-Q4_K_M"],
            runtime: {
              provider: "ollama",
              installed: true,
              server_running: true,
              model_loaded: true,
              effective_model_id: "gemma-4-E2B-it-Q4_K_M",
              loaded_model: "gemma-4-E2B-it-Q4_K_M",
              base_url: "http://127.0.0.1:11434/v1",
              context_length: 32768,
              capabilities: { start_stop: true, load_unload: true, context_length: true },
              checked_at: Math.floor(Date.now() / 1000) - 2,
            },
          },
        });
      }
      if (url === "/api/llm/provider/logs") {
        return Promise.resolve({ data: { logs: { entries: [], cursor: 0, next_cursor: 0 } } });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({ data: { exists: false, verified: false } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "ollama",
          transformerModel: "ollama",
        },
      },
    );

    expect(
      await screen.findByText("usable", { selector: ".runtime-panel-status" }),
    ).toBeInTheDocument();
    await expandRuntimeDetails();
    expect(
      screen.getByTitle(/Runtime endpoint: http:\/\/127.0.0.1:11434\/v1/i),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("gemma-4-E2B-it-Q4_K_M")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^load$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^unload$/i })).toBeInTheDocument();
    expect(screen.getByLabelText("context")).toBeInTheDocument();
  });

  it("shows direct-local timing and actionable preflight guidance", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: {
            runtime: {
              mode: "local",
              model: "gemma-4-E2B-it",
              effective_model_id: "gemma-4-E2B-it",
              load_state: "loading",
              load_started_at: Math.floor(Date.now() / 1000) - 9,
              local_loader: "image_text_to_text",
              supports_images: true,
              memory: { gpu: [], system: {} },
              preflight: {
                python_executable: "D:/notebooks/float_dev/backend/.venv/Scripts/python.exe",
                missing_packages: ["torch", "transformers"],
                missing_runtime_components: [
                  "AutoModelForMultimodalLM or AutoModelForImageTextToText",
                ],
                hint:
                  "Direct local loading uses the backend Python at 'D:/notebooks/float_dev/backend/.venv/Scripts/python.exe', but this environment is missing torch, transformers.",
              },
            },
          },
        });
      }
      if (typeof url === "string" && url.startsWith("/api/models/verify/")) {
        return Promise.resolve({
          data: { exists: true, verified: true, installed_bytes: 1024 },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({ data: { models: [], runtime: {} } });
      }
      if (url === "/api/llm/provider/logs") {
        return Promise.resolve({ data: { logs: { entries: [], cursor: 0, next_cursor: 0 } } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          backendMode: "local",
          localModel: "gemma-4-E2B-it",
          transformerModel: "gemma-4-E2B-it",
        },
      },
    );

    fireEvent.click(
      screen.getByRole("button", { name: /expand runtime details/i }),
    );

    expect(await screen.findByText(/loading started/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Backend Python: D:\/notebooks\/float_dev\/backend\/\.venv\/Scripts\/python\.exe/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Missing direct-local packages: torch, transformers\./i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Missing transformers loader classes:/i)).toBeInTheDocument();
  });

  it("treats pointer presses and clicks as one collapse action", async () => {
    const onToggle = vi.fn();

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={onToggle}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady={false}
        onRefreshAgents={() => {}}
      />,
    );

    const collapseButton = screen.getByRole("button", {
      name: /collapse agent console/i,
    });
    fireEvent.pointerDown(collapseButton, { button: 0 });
    fireEvent.click(collapseButton);

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("uses clear agent-card controls for activity, compact mode, and hidden cards", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "active",
        updatedAt: now,
        summary: "Latest work",
        events: Array.from({ length: 7 }, (_, index) => ({
          type: "thought",
          content: `Thought ${index + 1}`,
          timestamp: now - index,
        })),
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
    expect(screen.queryAllByText("Thought 7")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /expand agent card/i })).toHaveTextContent("+");

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getAllByText("Thought 7").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /show full activity/i }));
    expect(screen.getByRole("button", { name: /show recent activity/i })).toBeInTheDocument();

    const card = screen.getByRole("heading", { name: /calendar-sync/i }).closest(".agent-card");
    fireEvent.click(card);
    expect(screen.getByRole("button", { name: /expand agent card/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));

    fireEvent.click(screen.getByRole("button", { name: /compact agent card/i }));
    expect(screen.queryAllByText("Thought 7")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /expand agent card/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide agent card/i }));
    expect(screen.queryByRole("heading", { name: /calendar-sync/i })).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden console cards/i,
    });
    expect(showHiddenButton).toHaveTextContent("hidden (1)");

    fireEvent.click(showHiddenButton);
    expect(await screen.findByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
  });

  it("shows delegated-run metadata and sends console control actions", async () => {
    const now = Date.now() / 1000;
    const onRefreshAgents = vi.fn().mockResolvedValue(undefined);
    const agents = [
      {
        id: "task:agent-1",
        label: "delegated worker",
        status: "active",
        updatedAt: now,
        summary: "Working on the verification pass",
        workflow: {
          id: "architect_planner",
          label: "Architect / Planner",
          role: "architect",
        },
        provenance: {
          kind: "fork",
          parent_session_id: "sess-parent",
          parent_message_id: "msg-parent",
        },
        handoff: {
          summary: "Verify the search result before replying.",
          open_goals: [{ id: "goal-1", title: "verify", status: "open" }],
          pending_approvals: [{ id: "approval-1", label: "search tool" }],
        },
        controls: {
          available: ["pause", "redirect", "stop"],
          modes: {
            pause: "soft",
            redirect: "queued_request",
            stop: "runtime_revoke",
          },
        },
        events: [
          {
            type: "thought",
            content: "Working on the verification pass",
            timestamp: now,
          },
        ],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={onRefreshAgents}
      />,
    );

    expect(screen.getByRole("button", { name: /expand agent card/i })).toHaveTextContent("+");
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(await screen.findByText(/Architect \/ Planner · architect/i)).toBeInTheDocument();
    expect(screen.getByText(/fork of sess-parent from msg-parent/i)).toBeInTheDocument();
    expect(
      screen.getByText(/handoff: Verify the search result before replying\. \(1 goal, 1 approval\)/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /pause delegated run/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/agents/console/task%3Aagent-1/pause", {
        note: "",
        workflow: "",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: /redirect delegated run/i }));
    fireEvent.change(screen.getByLabelText(/redirect note/i), {
      target: { value: "Take the verifier pass next." },
    });
    fireEvent.change(screen.getByLabelText(/workflow target/i), {
      target: { value: "mini_execution" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send redirect/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/agents/console/task%3Aagent-1/redirect", {
        note: "Take the verifier pass next.",
        workflow: "mini_execution",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: /stop delegated run/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/agents/console/task%3Aagent-1/stop", {
        note: "",
        workflow: "",
      });
    });
    expect(onRefreshAgents).toHaveBeenCalled();
  });

  it("labels subchat agents clearly and opens the subchat conversation", async () => {
    const now = Date.now() / 1000;
    const onOpenConversation = vi.fn();
    const agents = [
      {
        id: "7d977fc1-f520-4ac9-9f58-017f67fddab3",
        label: "7d977fc1-f520-4ac9-9f58-017f67fddab3",
        status: "active",
        updatedAt: now,
        summary: "Checking layout and clickthrough state",
        provenance: {
          kind: "subchat",
          parent_session_id: "sess-parent",
          parent_message_id: "msg-parent",
          branch_session_id: "sess-child",
          label: "Investigate agent console",
        },
        events: [],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        onOpenConversation={onOpenConversation}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: /investigate agent console/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/checking layout and clickthrough state/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^investigate agent console$/i }));
    expect(onOpenConversation).toHaveBeenCalledWith(
      "sess-child",
      "Investigate agent console",
    );
    onOpenConversation.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /open subchat investigate agent console/i }));

    expect(onOpenConversation).toHaveBeenCalledWith(
      "sess-child",
      "Investigate agent console",
    );
  });

  it("hides empty opaque agent records instead of showing active UUID cards", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "6d9a911c-98be-4084-8fdd-98b75bdec64e",
        label: "6d9a911c-98be-4084-8fdd-98b75bdec64e",
        status: "active",
        updatedAt: now,
        events: [],
      },
      {
        id: "agent-1",
        label: "reader",
        status: "active",
        updatedAt: now,
        events: [{ type: "thought", content: "Reading files", timestamp: now }],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /reader/i })).toBeInTheDocument();
    expect(
      screen.queryByText(/6d9a911c-98be-4084-8fdd-98b75bdec64e/i),
    ).not.toBeInTheDocument();
    const backgroundRegion = screen.getByRole("region", { name: /background/i });
    expect(within(backgroundRegion).getByTitle(/0 active, 0 reflections, 0 queue updates/i)).toBeInTheDocument();
  });

  it("shortens useful opaque agent records when no readable label is available", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "6d9a911c-98be-4084-8fdd-98b75bdec64e",
        label: "6d9a911c-98be-4084-8fdd-98b75bdec64e",
        status: "active",
        updatedAt: now,
        summary: "Background import is running",
        events: [{ type: "thought", content: "Indexing", timestamp: now }],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /agent 6d9a911c/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /6d9a911c-98be-4084-8fdd-98b75bdec64e/i }),
    ).not.toBeInTheDocument();
  });

  it("lets standalone write history minimize and restore from the hidden console button", async () => {
    const actions = [
      {
        id: "action-1",
        conversation_id: "sess-123",
        conversation_label: "Current chat",
        response_id: "msg-1234",
        response_label: "response 1234",
        kind: "write",
        name: "write_file",
        summary: "Draft reply",
        status: "applied",
        created_at_ts: Date.now() / 1000,
        revertible: true,
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        actions={actions}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /write history/i })).toBeInTheDocument();
    expect(screen.queryByText(/draft reply/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand write history/i })).toHaveTextContent("+");

    fireEvent.click(screen.getByRole("button", { name: /expand write history/i }));
    expect(screen.getByRole("button", { name: /minimize write history/i })).toHaveTextContent("-");
    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide write history/i }));
    expect(screen.queryByRole("heading", { name: /write history/i })).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden console cards/i,
    });
    expect(showHiddenButton).toHaveTextContent("hidden (1)");

    fireEvent.click(showHiddenButton);
    expect(await screen.findByRole("heading", { name: /write history/i })).toBeInTheDocument();
  });

  it("keeps standalone write history visible even when tool cards expose contextual history", async () => {
    const now = Date.now() / 1000;
    const actions = [
      {
        id: "action-1",
        conversation_id: "sess-123",
        conversation_label: "Current chat",
        response_id: "msg-1234",
        response_label: "response 1234",
        kind: "write",
        name: "write_file",
        summary: "Draft reply",
        status: "applied",
        created_at_ts: now,
        revertible: true,
      },
    ];
    const agents = [
      {
        id: "agent-1",
        label: "writer",
        status: "active",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "write_file",
            args: { path: "note", content: "i miss you" },
            result: { status: "invoked", message: "written" },
            status: "invoked",
            message_id: "msg-1234",
            chain_id: "msg-1234",
            timestamp: now,
          },
        ],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        actions={actions}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
    );

    expect(await screen.findByRole("heading", { name: /write history/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getAllByRole("button", { name: /open work history \(1\)/i }).length).toBeGreaterThan(0);
  });

  it("explains when tool details move inline and hides duplicate tool rows", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-1",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "invoked",
            timestamp: now,
          },
          {
            type: "thought",
            content: "Still working",
            timestamp: now + 1,
          },
        ],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          toolDisplayMode: "inline",
        },
      },
    );

    expect(
      await screen.findByText(
        /Tool details are inline in chat\. The console is showing thoughts, messages, and tasks only\./i,
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(screen.getAllByText("Still working").length).toBeGreaterThan(0);
    expect(screen.queryByText("calendar.lookup")).not.toBeInTheDocument();
  });

  it("renders a synthetic console tool card when chat tool state exists without agent events", async () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          toolDisplayMode: "both",
          conversation: [
            {
              role: "ai",
              id: "ai-tool-error",
              text: "",
              timestamp: "2026-04-22T16:17:00Z",
              tools: [
                {
                  name: "write_file",
                  args: { path: "////w..5%7*/*{{} /", content: "i miss you" },
                  status: "error",
                  result: { status: "error", message: "Invalid signature" },
                },
              ],
            },
          ],
        },
      },
    );

    expect(await screen.findByRole("heading", { name: "write_file" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /expand activity details/i }));
    expect(screen.getAllByText(/Invalid signature/i).length).toBeGreaterThan(0);
    const failedToolRow = document.querySelector(
      '.agent-activity-tool[data-tool-status="error"]',
    );
    expect(failedToolRow).toBeInTheDocument();
    expect(within(failedToolRow).getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
    expect(
      within(failedToolRow).getByRole("button", { name: /edit & retry/i }),
    ).toBeInTheDocument();
  });

  it("uses real tool names and source order for synthetic conversation tool batches", async () => {
    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={[]}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          toolDisplayMode: "both",
          conversation: [
            {
              role: "ai",
              id: "ai-tool-order",
              text: "",
              timestamp: "2026-04-22T16:17:00Z",
              tools: [
                {
                  tool: "search_web",
                  args: { query: "Float project privacy-first" },
                  status: "proposed",
                },
                {
                  name: "recall",
                  args: { query: "user profile preferences Float" },
                  status: "proposed",
                },
              ],
            },
          ],
        },
      },
    );

    expect(
      await screen.findByRole("heading", { name: "search_web + recall" }),
    ).toBeInTheDocument();
    const visibleToolNames = Array.from(
      document.querySelectorAll(".agent-activity-name-button"),
    ).map((node) => node.textContent);
    expect(visibleToolNames.slice(0, 2)).toEqual(["search_web", "recall"]);
  });

  it("keeps tool rows visible in auto mode", async () => {
    const now = Date.now() / 1000;
    const agents = [
      {
        id: "agent-auto-tools",
        label: "calendar-sync",
        status: "pending",
        updatedAt: now,
        events: [
          {
            type: "tool",
            name: "calendar.lookup",
            args: { query: "today" },
            status: "invoked",
            timestamp: now,
          },
          {
            type: "thought",
            content: "Still working",
            timestamp: now + 1,
          },
        ],
      },
    ];

    renderWithGlobalState(
      <AgentConsole
        collapsed={false}
        onToggle={() => {}}
        streamEnabled={false}
        onStreamToggle={() => {}}
        agents={agents}
        onSelectMessage={() => {}}
        backendReady
        onRefreshAgents={() => {}}
      />,
      {
        stateOverrides: {
          toolDisplayMode: "auto",
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /expand agent card/i }));
    expect(await screen.findByText("calendar.lookup")).toBeInTheDocument();
    expect(
      screen.queryByText(
        /Tool details are inline in chat\. The console is showing thoughts, messages, and tasks only\./i,
      ),
    ).not.toBeInTheDocument();
  });
});
