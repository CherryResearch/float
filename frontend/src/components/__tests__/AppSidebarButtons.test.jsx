import React from "react";
import { vi } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";
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
    globalThis.WebSocket = OriginalWebSocket;
  });

  test("click opens left sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const leftOpenButton = screen.getByTitle("Show history sidebar");
    fireEvent.click(leftOpenButton);

    await waitFor(() =>
      expect(screen.queryByTitle("Show history sidebar")).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed");
  });

  test("primary pointer press opens left sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const leftOpenButton = screen.getByTitle("Show history sidebar");
    fireEvent.pointerDown(leftOpenButton, { button: 0 });

    await waitFor(() =>
      expect(screen.queryByTitle("Show history sidebar")).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed");
  });

  test("click opens right sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    const rightOpenButton = screen.getByTitle("Show agent console");
    fireEvent.click(rightOpenButton);

    await waitFor(() =>
      expect(screen.queryByTitle("Show agent console")).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".sidebar.right-sidebar")).not.toHaveClass("collapsed");
  });

  test("outside pointer press closes an open sidebar in narrow layout", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByTitle("Show history sidebar"));
    await waitFor(() =>
      expect(document.querySelector(".sidebar.left-sidebar")).not.toHaveClass("collapsed"),
    );

    fireEvent.pointerDown(document.body, { button: 0 });

    await waitFor(() =>
      expect(document.querySelector(".sidebar.left-sidebar")).toHaveClass("collapsed"),
    );
    expect(screen.getByTitle("Show history sidebar")).toBeInTheDocument();
  });

  test("settings route uses the inner settings scroll shell", async () => {
    window.history.pushState({}, "", "/settings");
    const { default: App } = await import("../App");
    render(<App />);

    expect(document.querySelector(".main-chat")).toHaveClass("main-chat--settings");
  });
});
