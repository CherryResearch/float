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
