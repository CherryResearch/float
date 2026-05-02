import React from "react";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {
        backendMode: "api",
        apiStatus: "online",
        sessionId: "sess-123",
        sessionName: "Current Session",
      },
      setState: vi.fn(),
    }),
  };
});

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  default: axiosMocks,
}));

import HistorySidebar, {
  formatConversationDate,
  getHorizontalScrollIndicatorMetrics,
} from "../HistorySidebar";
import { GlobalContext } from "../../main";

const baseGlobalState = {
  backendMode: "api",
  apiStatus: "online",
  sessionId: "sess-123",
  sessionName: "Current Session",
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

describe("HistorySidebar", () => {
  beforeEach(() => {
    axiosMocks.get.mockReset();
    axiosMocks.post.mockReset();
    axiosMocks.delete.mockReset();
    axiosMocks.post.mockResolvedValue({ data: { status: "ok" } });
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({ data: { conversations: [] } });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: {} });
      }
      if (url === "/api/threads/summary") {
        return Promise.resolve({ data: { summary: {} } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("keeps new chat separate from the overflow action rail", async () => {
    const { container } = renderWithGlobalState(
      <HistorySidebar collapsed={false} onToggle={() => {}} />,
    );

    await waitFor(() => {
      expect(axiosMocks.get).toHaveBeenCalledWith(
        "/api/conversations",
        expect.objectContaining({ params: { detailed: true } }),
      );
    });

    const leftRail = container.querySelector(".history-controls-left");
    const rightRail = container.querySelector(".history-controls-right");
    const overflowActions = container.querySelector(".history-actions");
    expect(leftRail).not.toBeNull();
    expect(rightRail).not.toBeNull();
    expect(overflowActions).not.toBeNull();

    const newChatButton = within(rightRail).getByRole("button", { name: /^new chat$/i });
    expect(rightRail).toContainElement(newChatButton);
    expect(overflowActions).not.toContainElement(newChatButton);
    expect(within(leftRail).getByRole("button", { name: /updated/i })).toBeInTheDocument();
    expect(within(overflowActions).getByRole("button", { name: /^import$/i })).toBeInTheDocument();
    expect(within(overflowActions).getByRole("button", { name: /fork/i })).toBeInTheDocument();
    expect(within(overflowActions).getByRole("button", { name: /new folder/i })).toBeInTheDocument();
  });

  it("treats pointer presses and clicks as one collapse action", async () => {
    const onToggle = vi.fn();
    renderWithGlobalState(<HistorySidebar collapsed={false} onToggle={onToggle} />);

    await waitFor(() => {
      expect(axiosMocks.get).toHaveBeenCalledWith(
        "/api/conversations",
        expect.objectContaining({ params: { detailed: true } }),
      );
    });

    const collapseButton = screen.getByRole("button", {
      name: /collapse history sidebar/i,
    });
    fireEvent.pointerDown(collapseButton, { button: 0 });
    fireEvent.click(collapseButton);

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("syncs text history when loading a different conversation", async () => {
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({
          data: {
            conversations: [
              {
                name: "sess-loaded",
                display_name: "Loaded thread",
                updated_at: "2026-04-22T00:10:00Z",
                message_count: 2,
              },
            ],
          },
        });
      }
      if (url === "/api/conversations/sess-loaded") {
        return Promise.resolve({
          data: {
            messages: [
              { role: "user", text: "fresh question" },
              { role: "ai", text: "fresh answer" },
            ],
          },
        });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: {} });
      }
      if (url === "/api/threads/summary") {
        return Promise.resolve({ data: { summary: {} } });
      }
      return Promise.resolve({ data: {} });
    });
    const setState = vi.fn();
    renderWithGlobalState(
      <HistorySidebar collapsed={false} onToggle={() => {}} />,
      {
        stateOverrides: {
          history: [{ role: "user", text: "stale question" }],
          conversation: [{ role: "user", text: "stale question" }],
        },
        setState,
      },
    );

    fireEvent.click(await screen.findByRole("button", { name: "Loaded thread" }));

    await waitFor(() => expect(setState).toHaveBeenCalled());
    const updater = setState.mock.calls.at(-1)?.[0];
    expect(typeof updater).toBe("function");
    const nextState = updater({
      ...baseGlobalState,
      history: [{ role: "user", text: "stale question" }],
      conversation: [{ role: "user", text: "stale question" }],
    });
    expect(nextState.sessionId).toBe("sess-loaded");
    expect(nextState.history).toEqual([
      { role: "user", text: "fresh question" },
      { role: "ai", text: "fresh answer" },
    ]);
  });

  it("clears carried-over history when starting a new chat", async () => {
    const setState = vi.fn();
    renderWithGlobalState(
      <HistorySidebar collapsed={false} onToggle={() => {}} />,
      {
        stateOverrides: {
          history: [{ role: "user", text: "stale question" }],
          conversation: [{ role: "user", text: "stale question" }],
        },
        setState,
      },
    );

    fireEvent.click(await screen.findByRole("button", { name: /^new chat$/i }));

    const updater = setState.mock.calls.at(-1)?.[0];
    expect(typeof updater).toBe("function");
    const nextState = updater({
      ...baseGlobalState,
      history: [{ role: "user", text: "stale question" }],
      conversation: [{ role: "user", text: "stale question" }],
    });
    expect(nextState.conversation).toEqual([]);
    expect(nextState.history).toEqual([]);
    expect(nextState.sessionId).toMatch(/^sess-\d+$/);
  });

  it("nests subchats under the active parent conversation", async () => {
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({
          data: {
            conversations: [
              {
                name: "sess-parent",
                display_name: "Main chat",
                updated_at: "2026-04-16T22:00:00Z",
                message_count: 2,
              },
              {
                name: "task-child",
                display_name: "Child subchat",
                updated_at: "2026-04-16T22:01:00Z",
                message_count: 2,
                provenance: {
                  kind: "subchat",
                  parent_session_id: "sess-parent",
                  parent_message_id: "assistant-1",
                  branch_session_id: "task-child",
                },
              },
            ],
          },
        });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: {} });
      }
      if (url === "/api/threads/summary") {
        return Promise.resolve({ data: { summary: {} } });
      }
      return Promise.resolve({ data: {} });
    });

    const { container } = renderWithGlobalState(
      <HistorySidebar collapsed={false} onToggle={() => {}} />,
      { stateOverrides: { sessionId: "sess-parent", sessionName: "Main chat" } },
    );

    expect(await screen.findByRole("button", { name: "Main chat" })).toBeInTheDocument();
    const childButtons = await screen.findAllByRole("button", {
      name: "Child subchat",
    });
    expect(childButtons).toHaveLength(1);
    expect(container.querySelector(".conversation-subchat-children")).not.toBeNull();
    expect(container.querySelector(".conversation-subchat-chip")).toHaveTextContent(
      "subchat",
    );
  });

  it("edits conversation privacy from the conversation modal and shows a compact badge", async () => {
    axiosMocks.get.mockImplementation((url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({
          data: {
            conversations: [
              {
                name: "notes/private-chat",
                display_name: "Private chat",
                updated_at: "2026-04-20T20:00:00Z",
                created_at: "2026-04-20T19:00:00Z",
                message_count: 3,
                privacy_mode: "protected",
                sensitivity: "protected",
              },
            ],
          },
        });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: {} });
      }
      if (url === "/api/threads/summary") {
        return Promise.resolve({ data: { summary: {} } });
      }
      if (url === "/api/conversations/notes%2Fprivate-chat/suggest-name") {
        return Promise.resolve({ data: { suggested_name: "Suggested title" } });
      }
      return Promise.resolve({ data: {} });
    });

    renderWithGlobalState(<HistorySidebar collapsed={false} onToggle={() => {}} />);

    const privateChatButton = await screen.findByRole("button", { name: "Private chat" });
    expect(privateChatButton).toBeInTheDocument();
    expect(screen.getByText("protected")).toBeInTheDocument();

    const privateChatRow = privateChatButton.closest(".conversation-item");
    expect(privateChatRow).not.toBeNull();
    fireEvent.click(
      within(privateChatRow).getByRole("button", { name: /conversation options/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

    expect(await screen.findByRole("heading", { name: /edit conversation/i })).toBeInTheDocument();
    const privacySelect = screen.getByDisplayValue("protected");
    expect(privacySelect).toHaveAttribute(
      "title",
      expect.stringMatching(/excluded from sync and external apis by default/i),
    );
    fireEvent.change(privacySelect, { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith(
        "/api/conversations/notes%2Fprivate-chat/rename",
        {
          new_name: "notes/private-chat",
          privacy_mode: "secret",
        },
      );
    });
  });
});

describe("HistorySidebar date fallback", () => {
  it("formats sess timestamps as MM-DD HH:MM", () => {
    const timestamp = new Date(2026, 2, 6, 14, 5).getTime();
    expect(formatConversationDate(`sess-${timestamp}`)).toBe("03-06 14:05");
  });

  it("supports nested storage keys and .json suffixes", () => {
    const timestamp = new Date(2026, 0, 2, 3, 4).getTime();
    expect(formatConversationDate(`folders/notes/sess-${timestamp}.json`)).toBe(
      "01-02 03:04",
    );
  });

  it("returns null for non-session keys", () => {
    expect(formatConversationDate("conversation")).toBeNull();
  });
});

describe("HistorySidebar scroll indicator metrics", () => {
  it("hides the indicator when controls fit in the viewport", () => {
    expect(
      getHorizontalScrollIndicatorMetrics({
        scrollLeft: 0,
        clientWidth: 280,
        scrollWidth: 280,
      }),
    ).toEqual({
      hasOverflow: false,
      thumbWidth: 1,
      thumbOffset: 0,
    });
  });

  it("reports thumb size and position for overflowing controls", () => {
    expect(
      getHorizontalScrollIndicatorMetrics({
        scrollLeft: 75,
        clientWidth: 200,
        scrollWidth: 500,
      }),
    ).toEqual({
      hasOverflow: true,
      thumbWidth: 0.4,
      thumbOffset: 0.15,
    });
  });
});
