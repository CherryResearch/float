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
    mockState.conversation = [
      { role: "ai", id: "msg-1", text: "Requested tools.", tools: [] },
    ];
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

  test("tracks server-owned auto decisions without posting a duplicate accept", async () => {
    axiosMocks.get.mockResolvedValue({ data: { agents: [] } });
    axiosMocks.post.mockResolvedValue({
      data: { message: "done", metadata: {}, tools_used: [] },
    });

    const { default: App } = await import("../App");
    render(<App />);

    await waitFor(() => expect(wsInstances.length).toBeGreaterThan(0));
    const ws = wsInstances[0];
    await act(async () => {
      ws.emit({
        type: "tool",
        id: "tool-server-owned",
        name: "remember",
        args: { key: "one", value: "once" },
        status: "proposed",
        server_auto_decide: true,
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
      ws.emit({
        type: "tool",
        id: "tool-server-owned",
        name: "remember",
        args: { key: "one", value: "once" },
        status: "invoked",
        result: { status: "invoked", ok: true, data: "ok" },
        session_id: "sess-1",
        message_id: "msg-1",
        chain_id: "msg-1",
      });
    });

    await waitFor(() =>
      expect(
        axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue"),
      ).toHaveLength(1),
    );
    expect(
      axiosMocks.post.mock.calls.filter(([url]) => url === "/api/tools/decision"),
    ).toHaveLength(0);
  });

  test("does not auto-continue the same semantic tool batch twice when request ids change", async () => {
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

    const emitResolvedTool = async (id) => {
      await act(async () => {
        ws.emit({
          type: "tool",
          id,
          name: "remember",
          args: { key: "reddit_video_check", value: "same value" },
          status: "proposed",
          session_id: "sess-1",
          message_id: "msg-1",
          chain_id: "msg-1",
        });
        ws.emit({
          type: "tool",
          id,
          name: "remember",
          args: { key: "reddit_video_check", value: "same value" },
          status: "invoked",
          result: { status: "invoked", ok: true, message: null, data: "ok" },
          session_id: "sess-1",
          message_id: "msg-1",
          chain_id: "msg-1",
        });
      });
    };

    await emitResolvedTool("tool-a");

    await waitFor(() =>
      expect(
        axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue"),
      ).toHaveLength(1),
    );

    await emitResolvedTool("tool-b");

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(
      axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue"),
    ).toHaveLength(1);
  });

  test("allows the same semantic batch in a new regeneration attempt", async () => {
    axiosMocks.get.mockResolvedValue({ data: { agents: [] } });
    axiosMocks.post.mockResolvedValue({
      data: { message: "done", metadata: {}, tools_used: [] },
    });

    const { default: App } = await import("../App");
    const {
      announceToolContinuationAttemptReset,
      buildToolContinuationSignature,
    } = await import(
      "../../utils/toolContinuations"
    );
    render(<App />);

    await waitFor(() => expect(wsInstances.length).toBeGreaterThan(0));
    const ws = wsInstances[0];
    const emitResolvedRemember = async (id) => {
      await act(async () => {
        ws.emit({
          type: "tool",
          id,
          name: "remember",
          args: { key: "photo.owl", value: "same value" },
          status: "proposed",
          server_auto_decide: true,
          session_id: "sess-1",
          message_id: "msg-1",
          chain_id: "msg-1",
        });
        ws.emit({
          type: "tool",
          id,
          name: "remember",
          args: { key: "photo.owl", value: "same value" },
          status: "invoked",
          result: { status: "invoked", ok: true, data: "ok" },
          session_id: "sess-1",
          message_id: "msg-1",
          chain_id: "msg-1",
        });
      });
    };

    await emitResolvedRemember("attempt-one-tool");
    await waitFor(() =>
      expect(
        axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue"),
      ).toHaveLength(1),
    );

    const staleBatch = [
      {
        id: "attempt-two-tool",
        name: "remember",
        args: { key: "photo.owl", value: "same value" },
        status: "invoked",
        result: { status: "invoked", ok: true, data: "ok" },
      },
    ];
    mockState.conversation[0].metadata = {
      tool_continue_signature: buildToolContinuationSignature(staleBatch),
      tool_continue_semantic_signature: buildToolContinuationSignature(staleBatch, {
        includeIds: false,
      }),
    };

    act(() => {
      announceToolContinuationAttemptReset({
        sessionId: "sess-1",
        messageId: "msg-1",
      });
    });
    await emitResolvedRemember("attempt-two-tool");

    await waitFor(() =>
      expect(
        axiosMocks.post.mock.calls.filter(([url]) => url === "/api/chat/continue"),
      ).toHaveLength(2),
    );
  });
});
