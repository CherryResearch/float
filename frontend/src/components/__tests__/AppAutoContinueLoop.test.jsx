import React from "react";
import { vi } from "vitest";
import { render, waitFor, act } from "@testing-library/react";

const mockState = {
  backendMode: "api",
  apiStatus: "online",
  approvalLevel: "auto",
  apiModel: "test-model",
  sessionId: "sess-1",
  conversation: [
    { role: "ai", id: "msg-1", text: "Requested tools.", tools: [] },
  ],
  history: [],
  devMode: false,
};

const setStateMock = vi.fn();

vi.mock("/src/main.jsx", () => ({
  GlobalContext: (() => {
    const React = require("react");
    return React.createContext({ state: mockState, setState: setStateMock });
  })(),
}));

vi.mock("/src/components/Chat.jsx", () => ({ default: () => null }));
vi.mock("/src/components/HistorySidebar.jsx", () => ({ default: () => null }));
vi.mock("/src/components/AgentConsole.jsx", () => ({ default: () => null }));
vi.mock("/src/components/Settings.jsx", () => ({ default: () => null }));
vi.mock("/src/components/Visualization.jsx", () => ({ default: () => null }));
vi.mock("/src/components/KnowledgeViewer.jsx", () => ({ default: () => null }));
vi.mock("/src/components/DevPanel.jsx", () => ({ default: () => null }));
vi.mock("/src/components/TopBar.jsx", () => ({ default: () => null }));
vi.mock("/src/components/DownloadTray.jsx", () => ({ default: () => null }));
vi.mock("/src/components/Notifications.jsx", () => ({ default: () => null }));
vi.mock("/src/components/ErrorBoundary.jsx", () => ({
  default: ({ children }) => children,
}));
vi.mock("/src/components/NotFound.jsx", () => ({ default: () => null }));

const axiosMocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
}));

vi.mock("axios", () => ({
  default: axiosMocks,
}));

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

describe("Full Auto tool loop", () => {
  const wsInstances = [];
  const OriginalWebSocket = globalThis.WebSocket;

  class MockWebSocket {
    constructor(url) {
      this.url = url;
      wsInstances.push(this);
      setTimeout(() => this.onopen?.(), 0);
    }
    close() {
      this.onclose?.({ wasClean: true });
    }
    emit(data) {
      this.onmessage?.({ data: JSON.stringify(data) });
    }
  }

  beforeEach(() => {
    wsInstances.length = 0;
    setStateMock.mockClear();
    axiosMocks.post.mockReset();
    axiosMocks.get.mockReset();
    globalThis.WebSocket = MockWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = OriginalWebSocket;
  });

  test("retries auto-continue after an in-flight continue finishes", async () => {
    const firstContinue = deferred();
    let continueCalls = 0;

    axiosMocks.get.mockResolvedValue({ data: { agents: [] } });
    axiosMocks.post.mockImplementation((url) => {
      if (url === "/api/tools/decision") {
        return Promise.resolve({
          data: {
            status: "invoked",
            result: { status: "invoked", ok: true, message: null, data: { ok: true } },
          },
        });
      }
      if (url === "/api/chat/continue") {
        continueCalls += 1;
        if (continueCalls === 1) return firstContinue.promise;
        return Promise.resolve({ data: { message: "done", metadata: {}, tools_used: [] } });
      }
      return Promise.resolve({ data: {} });
    });

    const { default: App } = await import("../App");
    render(<App />);

    await waitFor(() => expect(wsInstances.length).toBeGreaterThan(0));
    const ws = wsInstances[0];
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await act(async () => {
      ws.emit({
        type: "tool",
        id: "tool-a",
        name: "search",
        args: { q: "a" },
        status: "proposed",
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
      ws.emit({
        type: "tool",
        id: "tool-a",
        name: "search",
        args: { q: "a" },
        status: "invoked",
        result: { status: "invoked", ok: true, message: null, data: { ok: "a" } },
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
    });

    await waitFor(() =>
      expect(axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue")).toHaveLength(1),
    );

    await act(async () => {
      ws.emit({
        type: "tool",
        id: "tool-b",
        name: "search",
        args: { q: "b" },
        status: "proposed",
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
      ws.emit({
        type: "tool",
        id: "tool-b",
        name: "search",
        args: { q: "b" },
        status: "invoked",
        result: { status: "invoked", ok: true, message: null, data: { ok: "b" } },
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
    });

    await act(async () => {
      firstContinue.resolve({ data: { message: "step 1", metadata: {}, tools_used: [] } });
    });

    await waitFor(() =>
      expect(axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue")).toHaveLength(2),
    );
  });
});
