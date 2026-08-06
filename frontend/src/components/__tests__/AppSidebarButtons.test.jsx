import React from "react";
import { vi } from "vitest";
import { act, render, fireEvent, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

const mockState = {
  backendMode: "api",
  apiStatus: "offline",
  approvalLevel: "auto",
  apiModel: "test-model",
  localModel: "",
  transformerModel: "",
  thinkingMode: "fast",
  sessionId: "sess-1",
  conversation: [],
  history: [],
  devMode: false,
  calendarEvents: [],
};

const setStateMock = vi.fn();

vi.mock("../../main", () => ({
  GlobalContext: (() => {
    const React = require("react");
    return React.createContext({ state: mockState, setState: setStateMock });
  })(),
}));

vi.mock("../Chat", () => ({ default: () => null }));
vi.mock("../HistorySidebar", () => ({
  default: ({ collapsed }) => (
    <div
      className={`sidebar left-sidebar${collapsed ? " collapsed" : ""}`}
      data-testid="history-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
    />
  ),
}));
vi.mock("../AgentConsole", () => ({
  default: ({ collapsed }) => (
    <div
      className={`sidebar right-sidebar${collapsed ? " collapsed" : ""}`}
      data-testid="agent-console"
      data-collapsed={collapsed ? "true" : "false"}
    />
  ),
}));
vi.mock("../Settings", () => ({ default: () => null }));
vi.mock("../Visualization", () => ({ default: () => null }));
vi.mock("../KnowledgeViewer", () => ({ default: () => null }));
vi.mock("../DevPanel", () => ({ default: () => null }));
vi.mock("../TopBar", () => ({ default: () => null }));
vi.mock("../DownloadTray", () => ({ default: () => null }));
vi.mock("../Notifications", () => ({ default: () => null }));
vi.mock("../ErrorBoundary", () => ({ default: ({ children }) => children }));
vi.mock("../NotFound", () => ({ default: () => null }));

vi.mock("axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

describe("App sidebar open buttons", () => {
  const OriginalWebSocket = globalThis.WebSocket;

  class MockWebSocket {
    constructor() {
      setTimeout(() => this.onopen?.(), 0);
    }
    close() {
      this.onclose?.({ wasClean: true });
    }
  }

  beforeEach(() => {
    vi.resetModules();
    globalThis.WebSocket = MockWebSocket;
    window.history.pushState({}, "", "/");
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 500,
    });
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      writable: true,
      value: 900,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.WebSocket = OriginalWebSocket;
  });

  test("click opens left sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const leftOpenButton = screen.getByRole("button", { name: "Open chat history" });
    fireEvent.click(leftOpenButton);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Open chat history" }),
      ).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed");
  });

  test("primary pointer press opens left sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const leftOpenButton = screen.getByRole("button", { name: "Open chat history" });
    fireEvent.pointerDown(leftOpenButton, { button: 0 });

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Open chat history" }),
      ).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed");
  });

  test("follow-up click after a mobile pointer press does not close the sidebar", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const leftOpenButton = screen.getByRole("button", { name: "Open chat history" });
    fireEvent.pointerDown(leftOpenButton, { button: 0 });
    await waitFor(() =>
      expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed"),
    );

    // Mobile browsers synthesize a click after pointerdown. By then the opener
    // is gone, so the click can retarget to the document and must be ignored.
    fireEvent.click(document.body);

    expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed");
    expect(
      screen.queryByRole("button", { name: "Open chat history" }),
    ).not.toBeInTheDocument();
  });

  test("click opens right sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const rightOpenButton = screen.getByRole("button", { name: "Open agent console" });
    fireEvent.click(rightOpenButton);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Open agent console" }),
      ).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.right-sidebar")).not.toHaveClass("collapsed");
  });

  test("outside pointer press closes an open sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Open chat history" }));
    await waitFor(() =>
      expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed"),
    );

    fireEvent.pointerDown(document.body, { button: 0 });

    await waitFor(() =>
      expect(document.querySelector(".sidebar.left-sidebar")).toHaveClass("collapsed"),
    );
    expect(screen.getByRole("button", { name: "Open chat history" })).toBeInTheDocument();
  });

  test("settings route uses the inner settings scroll shell", async () => {
    window.history.pushState({}, "", "/settings");
    const { default: App } = await import("../App");
    render(<App />);

    expect(document.querySelector(".main-chat")).toHaveClass("main-chat--settings");
  });

  test("starts with both sidebars collapsed in Pixel 9 landscape", async () => {
    window.innerWidth = 924;
    window.innerHeight = 412;
    const { default: App } = await import("../App");
    render(<App />);

    expect(screen.getByRole("button", { name: "Open chat history" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open agent console" })).toBeInTheDocument();
    expect(document.querySelector(".sidebar.left-sidebar")).toHaveClass("collapsed");
    expect(document.querySelector(".sidebar.right-sidebar")).toHaveClass("collapsed");
  });

  test("desktop hover leaves collapsed sidebars closed", async () => {
    vi.useFakeTimers();
    const { default: App } = await import("../App");
    render(<App />);
    const leftOpenButton = screen.getByRole("button", { name: "Open chat history" });
    const rightOpenButton = screen.getByRole("button", { name: "Open agent console" });

    window.innerWidth = 1920;
    window.innerHeight = 1080;
    fireEvent.mouseEnter(leftOpenButton);
    fireEvent.mouseEnter(rightOpenButton);
    act(() => {
      vi.advanceTimersByTime(1200);
    });

    expect(leftOpenButton).toBeInTheDocument();
    expect(rightOpenButton).toBeInTheDocument();
  });
});
