import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";
vi.mock("../../main", () => ({ GlobalContext: require("react").createContext({}) }));
import { GlobalContext } from "../../main";
import Chat from "../Chat";
import AgentConsole from "../AgentConsole";

describe("Chat request lifecycle", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
  const base = { conversation: [], history: [], sessionId: "a", backendMode: "api", apiStatus: "online", apiModel: "slow-model", approvalLevel: "all" };
  const renderStatefulChat = (initialState = base) => {
    let current;
    let update;
    let selectedMessage;
    function Harness() {
      const [state, setState] = React.useState(initialState);
      const [activeMessageId, setActiveMessageId] = React.useState(null);
      current = state;
      update = setState;
      selectedMessage = activeMessageId;
      return <GlobalContext.Provider value={{ state, setState }}><Chat activeMessageId={activeMessageId} setActiveMessageId={setActiveMessageId} /></GlobalContext.Provider>;
    }
    render(<MemoryRouter><Harness /></MemoryRouter>);
    return {
      state: () => current,
      selected: () => selectedMessage,
      navigate: (next) => act(() => update(next)),
      send: (text) => {
        fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
        fireEvent.click(screen.getAllByRole("button", { name: /send message/i })[0]);
      },
    };
  };

  it("keeps approval attention visible when detailed tools are console-only", () => {
    const onOpenConsole = vi.fn();
    const state = { ...base, toolDisplayMode: "console", conversation: [{ id: "m", role: "ai", text: "Requested recall", tools: [{ id: "t", name: "recall", status: "proposed", args: { query: "test" } }] }] };
    render(<MemoryRouter><GlobalContext.Provider value={{ state, setState: vi.fn() }}><Chat onOpenConsole={onOpenConsole} /></GlobalContext.Provider></MemoryRouter>);
    expect(screen.getByText("Approval needed: recall")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review in Agent Console" }));
    expect(onOpenConsole).toHaveBeenCalledWith({ chainId: "m", toolId: "t" });
  });

  it("opens the matching compact console card from the in-chat review link", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    const tool = { id: "review-tool", name: "recall", status: "proposed", args: { query: "review this query" } };
    const state = { ...base, toolDisplayMode: "console", conversation: [{ id: "review-message", role: "ai", text: "Requested recall", tools: [tool] }] };
    const agents = [{ id: "review-agent", label: "recall", status: "pending", session_id: "a", events: [{ ...tool, type: "tool", message_id: "review-message", session_id: "a" }] }];
    function Harness() {
      const [focus, setFocus] = React.useState(null);
      return <GlobalContext.Provider value={{ state, setState: vi.fn() }}>
        <Chat onOpenConsole={setFocus} />
        <AgentConsole collapsed={false} agents={agents} focus={focus} backendReady />
      </GlobalContext.Provider>;
    }
    const { container } = render(<MemoryRouter><Harness /></MemoryRouter>);
    const card = () => container.querySelector('[data-agent-id="review-agent"]');
    await waitFor(() => expect(card()).toHaveClass("compact"));
    fireEvent.click(screen.getByRole("button", { name: "Review in Agent Console" }));
    await waitFor(() => expect(card()).not.toHaveClass("compact"));
    const row = card().querySelector('[data-tool-id="review-tool"]');
    expect(row).not.toBeNull();
    expect(row.querySelector(".agent-activity-body")).not.toBeNull();
    expect(row.querySelector("button.tool-action-btn.accept")).toBeEnabled();
    expect(row.querySelector("button.tool-action-btn.deny")).toBeEnabled();
    expect(post.mock.calls.filter(([url]) => url === "/api/tools/decision")).toHaveLength(0);
  });

  it.each(["resolve", "reject"])("does not apply a late %s to a different conversation", async (outcome) => {
    let finish;
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const post = vi.spyOn(axios, "post").mockImplementation((url) => url === "/api/chat"
      ? new Promise((resolve, reject) => { finish = outcome === "resolve" ? resolve : reject; })
      : Promise.resolve({ data: {} }));
    vi.spyOn(console, "error").mockImplementation(() => {});
    let update;
    let current;
    const active = vi.fn();
    function Harness() {
      const [state, setState] = React.useState(base);
      update = setState;
      current = state;
      return <GlobalContext.Provider value={{ state, setState }}><Chat setActiveMessageId={active} /></GlobalContext.Provider>;
    }
    render(<MemoryRouter><Harness /></MemoryRouter>);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "hello" } });
    fireEvent.click(screen.getAllByRole("button", { name: /send message/i })[0]);
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    act(() => update({ ...base, sessionId: "b" }));
    await act(async () => finish(outcome === "resolve"
      ? { data: { message: "old answer", metadata: { status: "complete" } } }
      : new Error("old failure")));
    expect(current.sessionId).toBe("b");
    expect(current.conversation).toEqual([]);
    expect(current.history).toEqual([]);
    expect(post.mock.calls.filter(([url]) => url === "/api/chat")).toHaveLength(1);
    expect(screen.queryByText(/old answer|old failure/)).not.toBeInTheDocument();
  });

  it.each([
    ["complete", "resolve"], ["pending", "resolve"],
    ["complete", "reject"], ["pending", "reject"],
  ])("preserves canonical %s state when an old HTTP request settles by %s after reopening or continuation", async (status, outcome) => {
    let finish;
    let messageId;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const post = vi.spyOn(axios, "post").mockImplementation((url, payload) => {
      if (url !== "/api/chat") return Promise.resolve({ data: {} });
      messageId = payload.message_id;
      return new Promise((resolve, reject) => { finish = outcome === "resolve" ? resolve : reject; });
    });
    const view = renderStatefulChat();
    view.send("hello");
    await waitFor(() => expect(messageId).toBeTruthy());
    if (status === "complete") view.navigate({ ...base, sessionId: "b" });
    const saved = {
      ...base,
      conversation: [
        { id: `${messageId}:user`, role: "user", text: "hello" },
        { id: messageId, role: "ai", text: "Saved completed answer", metadata: {
          status,
          timing: status === "complete" ? { completed_at: 123 } : { continuation_started_at: 123 },
        } },
      ],
      history: [{ role: "user", text: "hello" }, { role: "ai", text: "Saved completed answer" }],
    };
    view.navigate(saved);
    const writesBefore = post.mock.calls.filter(([url]) => url === "/api/history").length;
    await act(async () => finish(outcome === "resolve"
      ? { data: { message: "Old transport answer", metadata: { status: "complete" } } }
      : new Error("Old transport failure")));
    expect(view.state().conversation).toEqual(saved.conversation);
    expect(view.state().history).toEqual(saved.history);
    expect(post.mock.calls.filter(([url]) => url === "/api/history")).toHaveLength(writesBefore);
    expect(post.mock.calls.filter(([url]) => url === "/api/chat")).toHaveLength(1);
    expect(screen.queryByText("Old transport failure")).not.toBeInTheDocument();
  });

  it.each(["b", "a"])("does not clear %s's newer error when A's stopped regeneration rejects late", async (destination) => {
    let rejectRegeneration;
    let regenerationSignal;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "post").mockImplementation((url, payload, config) => {
      if (url !== "/api/chat") return Promise.resolve({ data: {} });
      if (payload.regenerate) {
        regenerationSignal = config.signal;
        return new Promise((_resolve, reject) => { rejectRegeneration = reject; });
      }
      return Promise.reject(new Error("Current B failure"));
    });
    const view = renderStatefulChat({ ...base, backendMode: "server", transformerModel: "local-model", conversation: [
      { id: "saved:user", role: "user", text: "hello" },
      { id: "saved", role: "ai", text: "Previous answer", metadata: { status: "complete", mode: "server", model: "local-model" } },
    ] });
    fireEvent.click(screen.getByLabelText("Regenerate response"));
    await waitFor(() => expect(rejectRegeneration).toBeTypeOf("function"));
    fireEvent.click(screen.getByRole("button", { name: /stop generation/i }));
    expect(regenerationSignal.aborted).toBe(true);
    view.navigate({ ...base, sessionId: "b" });
    if (destination === "a") view.navigate({ ...base });
    view.send("message b");
    await waitFor(() => expect(screen.getAllByText("Current B failure").length).toBeGreaterThan(0));
    const bBefore = view.state();
    await act(async () => rejectRegeneration(Object.assign(new Error("cancelled"), { code: "ERR_CANCELED" })));
    expect(screen.getAllByText("Current B failure").length).toBeGreaterThan(0);
    expect(view.state()).toEqual(bBefore);
  });

  const savedTurn = (mode, status, text = "Original answer") => ({
    ...base,
    backendMode: mode,
    transformerModel: "server-model",
    conversation: [
      { id: "owned:user", role: "user", text: "hello" },
      { id: "owned", role: "ai", text, metadata: { status, mode, model: "slow-model", timing: { completed_at: 200 }, ...(status === "partial" ? { unresolved_tool_loop: true } : {}) } },
    ],
    history: [{ role: "user", text: "hello" }, { role: "ai", text }],
  });

  it.each(["regenerate", "continue"].flatMap((action) =>
    ["api", "server"].flatMap((mode) => ["resolve", "reject"].map((outcome) => [action, mode, outcome])),
  ))("keeps the reopened saved turn after late %s %s %s", async (action, mode, outcome) => {
    let settle;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const endpoint = action === "regenerate" ? "/api/chat" : "/api/chat/continue";
    const post = vi.spyOn(axios, "post").mockImplementation((url) => url === endpoint
      ? new Promise((resolve, reject) => { settle = outcome === "resolve" ? resolve : reject; })
      : Promise.resolve({ data: {} }));
    const initial = savedTurn(mode, action === "regenerate" ? "complete" : "partial");
    const view = renderStatefulChat(initial);
    fireEvent.click(screen.getByLabelText(action === "regenerate" ? "Regenerate response" : "Continue generating"));
    await waitFor(() => expect(settle).toBeTypeOf("function"));
    view.navigate({ ...base, sessionId: "b" });
    // Legacy records can share a completion timestamp; changed saved text still wins.
    const canonical = savedTurn(mode, "complete", "Newer canonical answer");
    view.navigate(canonical);
    const writesBefore = post.mock.calls.filter(([url]) => url === "/api/history").length;
    await act(async () => settle(outcome === "resolve"
      ? { data: { message: "Obsolete answer", metadata: { status: "complete", timing: { completed_at: 100 }, warning: "Obsolete warning" } } }
      : new Error("Obsolete failure")));
    expect(view.state().conversation).toEqual(canonical.conversation);
    expect(view.state().history).toEqual(canonical.history);
    expect(post.mock.calls.filter(([url]) => url === "/api/history")).toHaveLength(writesBefore);
    expect(screen.queryAllByText("Obsolete warning")).toHaveLength(0);
    expect(screen.queryAllByText("Obsolete failure")).toHaveLength(0);
  });

  it.each(["regenerate", "continue"].flatMap((action) => ["api", "server"].map((mode) => [action, mode])))("accepts the same pending %s %s turn after reopening", async (action, mode) => {
      let finish;
      vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
      const endpoint = action === "regenerate" ? "/api/chat" : "/api/chat/continue";
      const post = vi.spyOn(axios, "post").mockImplementation((url) => url === endpoint
        ? new Promise((resolve) => { finish = resolve; })
        : Promise.resolve({ data: {} }));
      const initial = savedTurn(mode, action === "regenerate" ? "complete" : "partial");
      const view = renderStatefulChat(initial);
      fireEvent.click(screen.getByLabelText(action === "regenerate" ? "Regenerate response" : "Continue generating"));
      await waitFor(() => expect(finish).toBeTypeOf("function"));
      view.navigate({ ...base, sessionId: "b" });
      view.navigate({ ...initial, conversation: initial.conversation.map((message) => message.role === "ai"
        ? { ...message, metadata: { ...message.metadata, status: "generating", timing: {} } } : message) });
      await act(async () => finish({ data: { message: "Fresh background answer", metadata: { status: "complete", timing: { completed_at: 300 } } } }));
      expect(view.state().conversation[1].text).toContain("Fresh background answer");
      expect(view.state().conversation[1].metadata.timing.completed_at).toBe(300);
      expect(view.state().history.at(-1).text).toBe(view.state().conversation[1].text);
      expect(post.mock.calls.filter(([url]) => url === endpoint)).toHaveLength(1);
    });

  it.each(["api", "server"].flatMap((mode) => [false, true].map((duplicate) => [mode, duplicate])))("preserves the in-flight %s tool continuation after reopening; duplicate click %s", async (mode, duplicate) => {
    let finish;
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const post = vi.spyOn(axios, "post").mockImplementation((url) => url === "/api/chat/continue"
      ? new Promise((resolve) => { finish = resolve; })
      : Promise.resolve({ data: {} }));
    const initial = savedTurn(mode, "partial");
    initial.conversation[1].tools = [{ id: "lock-tool", name: "tool_info", args: { name: "read_file" }, status: "complete", result: { schema: "ok" } }];
    const view = renderStatefulChat(initial);
    fireEvent.click(screen.getByLabelText("Continue generating"));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    view.navigate({ ...base, sessionId: "b" });
    view.navigate({ ...initial, conversation: initial.conversation.map((message) => message.role === "ai"
      ? { ...message, metadata: { ...message.metadata, status: "generating", timing: {} } } : message) });
    if (duplicate) fireEvent.click(screen.getByLabelText("Continue generating"));
    expect(post.mock.calls.filter(([url]) => url === "/api/chat/continue")).toHaveLength(1);
    await act(async () => finish({ data: { message: "Fresh legitimate answer", metadata: { status: "complete", timing: { completed_at: 300 } } } }));
    expect(view.state().conversation[1].text).toContain("Fresh legitimate answer");
    expect(view.state().conversation[1].metadata.timing.completed_at).toBe(300);
    expect(view.state().history.at(-1).text).toBe(view.state().conversation[1].text);
    expect(post.mock.calls.filter(([url]) => url === "/api/chat/continue")).toHaveLength(1);
  });

  it("keeps B's request and selected model active when A finishes late", async () => {
    const requests = [];
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "post").mockImplementation((url, payload, config) => url === "/api/chat"
      ? new Promise((resolve) => requests.push({ resolve, payload, signal: config.signal }))
      : Promise.resolve({ data: {} }));
    const view = renderStatefulChat();
    view.send("message a");
    await waitFor(() => expect(requests).toHaveLength(1));
    view.navigate({ ...base, sessionId: "b", apiModel: "new-model" });
    view.send("message b");
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests.map(({ payload }) => payload.model)).toEqual(["slow-model", "new-model"]);
    await act(async () => requests[0].resolve({ data: { message: "answer a", metadata: { status: "complete" } } }));
    expect(view.selected()).toBe(requests[1].payload.message_id);
    expect(screen.getByRole("button", { name: /stop generation/i })).toBeEnabled();
    expect(requests[1].signal.aborted).toBe(false);
    await act(async () => requests[1].resolve({ data: { message: "answer b", metadata: { status: "complete" } } }));
    expect(view.state().history).toEqual([{ role: "user", text: "message b" }, { role: "ai", text: "answer b" }]);
    expect(view.state().conversation.some((entry) => entry.text === "answer a")).toBe(false);
  });

  it("Stop aborts the actual transport signal and leaves one cancelled response", async () => {
    let requestSignal;
    const onAbort = vi.fn();
    vi.spyOn(axios, "get").mockResolvedValue({ data: {} });
    const post = vi.spyOn(axios, "post").mockImplementation((url, _payload, config) => {
      if (url !== "/api/chat") return Promise.resolve({ data: {} });
      requestSignal = config.signal;
      return new Promise((_resolve, reject) => {
        requestSignal.addEventListener("abort", () => {
          onAbort();
          const error = new Error("cancelled");
          error.code = "ERR_CANCELED";
          reject(error);
        }, { once: true });
      });
    });
    const view = renderStatefulChat();
    view.send("hello");
    await waitFor(() => expect(requestSignal).toBeDefined());
    expect(requestSignal.aborted).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /stop generation/i }));
    expect(requestSignal.aborted).toBe(true);
    expect(requestSignal.reason).toBe("user_cancelled");
    expect(onAbort).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(view.state().conversation[1].metadata.status).toBe("cancelled"));
    expect(view.state().conversation).toHaveLength(2);
    expect(post.mock.calls.filter(([url]) => url === "/api/chat")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /stop generation/i })).not.toBeInTheDocument();
  });
});
