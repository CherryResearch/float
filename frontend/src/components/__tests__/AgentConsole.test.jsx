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

describe("AgentConsole", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "get").mockImplementation((url) => {
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

    expect(await screen.findByText(/calendar-sync/i)).toBeInTheDocument();
    expect(screen.getAllByText(/calendar.lookup/i)[0]).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /accept/i })[0]).toBeInTheDocument();
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
    expect(screen.queryByText("Pear", { selector: "strong" })).not.toBeInTheDocument();

    const expandButton = screen.getByRole("button", { name: /expand sync inbox/i });
    expect(expandButton).toHaveTextContent("+");

    fireEvent.click(expandButton);

    expect(await screen.findByText("Pear", { selector: "strong" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /collapse sync inbox/i }),
    ).toHaveTextContent("-");
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
    expect(screen.getAllByText(/write_file/i)[0]).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByText(/work history \(1\)/i, { selector: "button" }));
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

    fireEvent.click(screen.getByRole("button", { name: /minimize draft reply/i }));
    expect(screen.getByRole("button", { name: /expand draft reply/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide draft reply/i }));
    expect(screen.queryByText(/draft reply/i)).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden write items/i,
    });
    expect(showHiddenButton).toHaveTextContent("show hidden (1)");

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

    fireEvent.click(
      await screen.findByRole("button", { name: /expand activity details/i }),
    );
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

    fireEvent.click(
      await screen.findByRole("button", { name: /expand activity details/i }),
    );
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

    expect(await screen.findByText("installed")).toBeInTheDocument();
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

  it("updates provider status label after refresh", async () => {
    let providerStatusCalls = 0;
    axios.get.mockImplementation((url) => {
      if (url === "/api/llm/local-status") {
        return Promise.resolve({
          data: { runtime: { mode: "local", model: "lmstudio", memory: { gpu: [], system: {} } } },
        });
      }
      if (url === "/api/llm/provider/status") {
        providerStatusCalls += 1;
        return Promise.resolve({
          data: {
            runtime:
              providerStatusCalls < 2
                ? {
                    provider: "lmstudio",
                    installed: false,
                    server_running: false,
                    model_loaded: false,
                    capabilities: { start_stop: true, context_length: true },
                  }
                : {
                    provider: "lmstudio",
                    installed: true,
                    server_running: true,
                    model_loaded: false,
                    capabilities: { start_stop: true, context_length: true },
                  },
          },
        });
      }
      if (url === "/api/llm/provider/models") {
        return Promise.resolve({ data: { models: [], runtime: {} } });
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

    expect(await screen.findByText("not installed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /refresh provider runtime status/i }));
    expect(await screen.findByText("server running")).toBeInTheDocument();
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
    expect(screen.getAllByText("Thought 7").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /show full activity/i }));
    expect(screen.getByRole("button", { name: /show recent activity/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /compact agent card/i }));
    expect(screen.queryAllByText("Thought 7")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /expand agent card/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide agent card/i }));
    expect(screen.queryByRole("heading", { name: /calendar-sync/i })).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden console cards/i,
    });
    expect(showHiddenButton).toHaveTextContent("show hidden (1)");

    fireEvent.click(showHiddenButton);
    expect(await screen.findByRole("heading", { name: /calendar-sync/i })).toBeInTheDocument();
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
    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /minimize write history/i }));
    expect(screen.getByRole("button", { name: /expand write history/i })).toBeInTheDocument();
    expect(screen.queryByText(/draft reply/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide write history/i }));
    expect(screen.queryByRole("heading", { name: /write history/i })).not.toBeInTheDocument();

    const showHiddenButton = screen.getByRole("button", {
      name: /show hidden console cards/i,
    });
    expect(showHiddenButton).toHaveTextContent("show hidden (1)");

    fireEvent.click(showHiddenButton);
    expect(await screen.findByRole("heading", { name: /write history/i })).toBeInTheDocument();
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
    expect(screen.getAllByText("Still working").length).toBeGreaterThan(0);
    expect(screen.queryByText("calendar.lookup")).not.toBeInTheDocument();
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

    expect(await screen.findByText("calendar.lookup")).toBeInTheDocument();
    expect(
      screen.queryByText(
        /Tool details are inline in chat\. The console is showing thoughts, messages, and tasks only\./i,
      ),
    ).not.toBeInTheDocument();
  });
});
