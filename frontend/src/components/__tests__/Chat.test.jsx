import React from "react";
import { vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";
import { GlobalContext } from "../../main";
import Chat, {
  formatMessageTimestampLabel,
  mergeAssistantMessageMetadata,
  mergeToolEntries,
  prepareComposerSubmission,
  resolveRegenerateRequestTarget,
} from "../Chat";

describe("Chat", () => {
  const renderChat = (stateOverrides = {}, props = {}) => {
    const state = {
      conversation: [],
      history: [],
      sessionId: "sess-test",
      backendMode: "api",
      approvalLevel: "all",
      ...stateOverrides,
    };
    return render(
      <GlobalContext.Provider value={{ state, setState: vi.fn() }}>
        <MemoryRouter>
          <Chat thoughts={[]} setActiveMessageId={() => {}} {...props} />
        </MemoryRouter>
      </GlobalContext.Provider>,
    );
  };

  const openFirstInlineToolCard = () => {
    const summary = document.querySelector(".inline-tool-list details.inline-tool summary");
    if (!summary) {
      throw new Error("Expected an inline tool summary to be rendered");
    }
    fireEvent.click(summary);
  };

  const getFirstInlineToolCard = () => document.querySelector(".inline-tool-list details.inline-tool");

  it("keeps timestamps compact until the conversation crosses into a new day", () => {
    const first = "2026-03-11T10:00:00";
    const laterSameDay = "2026-03-11T10:05:00";
    const nextDay = "2026-03-12T09:00:00";
    const timeOptions = {
      hour: "2-digit",
      minute: "2-digit",
    };
    const dateOptions = {
      month: "short",
      day: "numeric",
    };
    if (new Date(nextDay).getFullYear() !== new Date().getFullYear()) {
      dateOptions.year = "numeric";
    }

    expect(formatMessageTimestampLabel(first)).toBe(
      new Date(first).toLocaleTimeString([], timeOptions),
    );
    expect(formatMessageTimestampLabel(laterSameDay, first)).toBe(
      new Date(laterSameDay).toLocaleTimeString([], timeOptions),
    );
    expect(formatMessageTimestampLabel(nextDay, laterSameDay)).toBe(
      `${new Date(nextDay).toLocaleDateString([], dateOptions)} · ${new Date(nextDay).toLocaleTimeString([], timeOptions)}`,
    );
  });

  it("shows regenerate button for AI messages", () => {
    const state = {
      conversation: [
        { role: "user", text: "Hi", timestamp: "2024-01-01T00:00:00Z" },
        { role: "ai", id: "1", text: "Hello", timestamp: "2024-01-01T00:00:01Z" },
      ],
      history: [
        { role: "user", text: "Hi" },
        { role: "ai", text: "Hello" },
      ],
      sessionId: "sess-test",
      backendMode: "api",
      approvalLevel: "all",
    };
    const { getByLabelText } = render(
      <GlobalContext.Provider value={{ state, setState: vi.fn() }}>
        <MemoryRouter>
          <Chat thoughts={[]} setActiveMessageId={() => {}} />
        </MemoryRouter>
      </GlobalContext.Provider>,
    );
    expect(getByLabelText("Regenerate response")).toBeInTheDocument();
  });

  it("does not re-inject inline metadata payloads when merging continuation results", () => {
    const merged = mergeToolEntries(
      [],
      [],
      {
        inline_tool_payloads: [
          JSON.stringify({ tool: "tool_help", args: {} }),
          JSON.stringify({ tool: "computer.app.launch", args: { app: "browser" } }),
        ],
      },
      { includeInlineMetadata: false },
    );

    expect(merged).toEqual([]);
  });

  it("hides continue for a completed tool turn even if stale pending metadata remains", () => {
    renderChat({
      conversation: [
        {
          role: "user",
          id: "turn-1:user",
          text: "Remember this.",
        },
        {
          role: "ai",
          id: "turn-1",
          text: "Done - I updated your profile with these points.",
          metadata: {
            status: "complete",
            tool_response_pending: true,
            tool_continued: true,
          },
          tools: [
            {
              id: "tool-remember",
              name: "remember",
              status: "invoked",
              result: { status: "invoked", ok: true, data: "ok" },
            },
          ],
        },
      ],
      history: [
        { role: "user", text: "Remember this." },
        { role: "ai", text: "Done - I updated your profile with these points." },
      ],
    });

    expect(
      screen.queryByRole("button", { name: /continue generating/i }),
    ).not.toBeInTheDocument();
  });

  it("allows attachment-only sends without inventing prompt text", () => {
    expect(prepareComposerSubmission("   ", 1)).toEqual({
      displayMessage: "",
      shouldSend: true,
    });
    expect(prepareComposerSubmission("  hello  ", 0)).toEqual({
      displayMessage: "hello",
      shouldSend: true,
    });
    expect(prepareComposerSubmission("   ", 0)).toEqual({
      displayMessage: "",
      shouldSend: false,
    });
  });

  it("shows the resolved provider model in the assistant source label", () => {
    renderChat({
      conversation: [
        {
          role: "ai",
          id: "provider-msg",
          text: "Hello from LM Studio.",
          timestamp: "2024-01-01T00:00:01Z",
          metadata: {
            mode: "local",
            model: "lmstudio",
            provider: "lmstudio",
            model_requested: "lmstudio",
            model_received: "gemma4:e4b",
            model_resolved: "gemma4:e4b",
          },
        },
      ],
      history: [{ role: "ai", text: "Hello from LM Studio." }],
    });

    expect(screen.getByText("local/lmstudio:gemma4:e4b")).toBeInTheDocument();
  });

  it("sends explicit api mode for cloud chat requests", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/devices/register") {
        return Promise.resolve({ data: { device: { id: "device-test" } } });
      }
      if (url === "/api/devices/token") {
        return Promise.resolve({ data: { token: "token-test" } });
      }
      if (url === "/api/chat") {
        return Promise.resolve({
          data: { message: "Paris", metadata: {}, tools_used: [] },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-send-mode",
        apiStatus: "online",
        apiModel: "gpt-5.4",
      });

      fireEvent.change(screen.getByRole("textbox"), {
        target: { value: "What is the capital of France?" },
      });
      fireEvent.click(screen.getAllByRole("button", { name: /send message/i })[0]);

      await waitFor(() => {
        const chatCall = postSpy.mock.calls.find(([url]) => url === "/api/chat");
        expect(chatCall).toBeTruthy();
        expect(chatCall[1]).toEqual(
          expect.objectContaining({
            message: "What is the capital of France?",
            mode: "api",
            model: "gpt-5.4",
          }),
        );
      });
    } finally {
      postSpy.mockRestore();
      localStorage.clear();
    }
  });

  it("regenerates against the original turn backend target instead of the current picker", () => {
    const target = resolveRegenerateRequestTarget(
      {
        backendMode: "api",
        apiModel: "gpt-5",
        localModel: "lmstudio",
        transformerModel: "openai/gpt-oss-20b",
      },
      {
        id: "assistant-1",
        metadata: {
          mode: "local",
          model: "lmstudio",
          model_requested: "google/gemma-3-270m",
          model_resolved: "google/gemma-3-270m",
        },
      },
    );

    expect(target).toEqual({
      mode: "local",
      model: "google/gemma-3-270m",
    });
  });

  it("sends explicit api mode when regenerating an api response", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/chat") {
        return Promise.resolve({
          data: { message: "Updated answer", metadata: {}, tools_used: [] },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-regenerate-mode",
        apiModel: "gpt-5.4",
        conversation: [
          { role: "user", text: "Tell me a fact.", timestamp: "2024-01-01T00:00:00Z" },
          {
            role: "ai",
            id: "assistant-1",
            text: "Original answer",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: { mode: "api", model: "gpt-5.4" },
          },
        ],
        history: [
          { role: "user", text: "Tell me a fact." },
          { role: "ai", text: "Original answer" },
        ],
      });

      fireEvent.click(screen.getByLabelText("Regenerate response"));

      await waitFor(() => {
        const chatCall = postSpy.mock.calls.find(([url]) => url === "/api/chat");
        expect(chatCall).toBeTruthy();
        expect(chatCall[1]).toEqual(
          expect.objectContaining({
            message: "Tell me a fact.",
            mode: "api",
            model: "gpt-5.4",
            message_id: "assistant-1",
          }),
        );
      });
    } finally {
      postSpy.mockRestore();
      localStorage.clear();
    }
  });

  it("clears stale failure metadata when a later response completes successfully", () => {
    const merged = mergeAssistantMessageMetadata(
      {
        status: "error",
        error: "No model loaded",
        category: "model_missing",
        hint: "Load a model and retry.",
        status_code: 409,
      },
      {
        status: "complete",
        model: "gemma4:e4b",
      },
    );

    expect(merged).toEqual({
      status: "complete",
      model: "gemma4:e4b",
    });
  });

  it("keeps the composer to record, live, attach, and send primary actions", () => {
    const { getAllByRole, getByRole, queryByRole } = renderChat({
      sessionId: "sess-actions",
      apiStatus: "online",
    });

    expect(getAllByRole("button", { name: /record audio message/i }).length).toBeGreaterThan(0);
    expect(getAllByRole("button", { name: /live streaming mode/i }).length).toBeGreaterThan(0);
    const attachmentButtons = getAllByRole("button", { name: /open attachments/i });
    expect(attachmentButtons.length).toBeGreaterThan(0);
    expect(getAllByRole("button", { name: /send message/i }).length).toBeGreaterThan(0);
    expect(queryByRole("button", { name: /capture from camera/i })).not.toBeInTheDocument();

    fireEvent.click(attachmentButtons[0]);

    expect(getByRole("button", { name: /capture from camera/i })).toBeInTheDocument();
    expect(getByRole("button", { name: /capture from desktop/i })).toBeInTheDocument();
  });

  it("offers inline tool command completions and inserts the selected command", async () => {
    const getSpy = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/tools/catalog") {
        return Promise.resolve({
          data: {
            tools: [
              { name: "search_web", summary: "Search the web" },
              { name: "remember", summary: "Store a memory" },
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-command-completion",
        apiStatus: "online",
      });

      const composer = screen.getByRole("textbox");
      fireEvent.change(composer, {
        target: { value: "%re", selectionStart: 3, selectionEnd: 3 },
      });

      await waitFor(() => {
        expect(getSpy).toHaveBeenCalledWith("/api/tools/catalog");
      });

      expect(await screen.findByRole("option", { name: /remember/i })).toBeInTheDocument();

      fireEvent.keyDown(composer, { key: "Tab" });

      await waitFor(() => {
        expect(composer).toHaveValue("%remember ");
      });
      await waitFor(() => {
        expect(
          screen.queryByRole("listbox", { name: /command suggestions/i }),
        ).not.toBeInTheDocument();
      });
    } finally {
      getSpy.mockRestore();
    }
  });

  it("keeps the active autocomplete suggestion visible while arrowing through results", async () => {
    const getSpy = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/tools/catalog") {
        return Promise.resolve({
          data: {
            tools: [
              { name: "remember", summary: "Store a memory" },
              { name: "recall", summary: "Read memory" },
              { name: "reindex", summary: "Rebuild search" },
              { name: "replace", summary: "Replace text" },
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const restoreScrollIntoView = !Element.prototype.scrollIntoView;
    if (restoreScrollIntoView) {
      Object.defineProperty(Element.prototype, "scrollIntoView", {
        configurable: true,
        writable: true,
        value: () => {},
      });
    }
    const scrollSpy = vi
      .spyOn(Element.prototype, "scrollIntoView")
      .mockImplementation(() => {});

    try {
      renderChat({
        sessionId: "sess-command-navigation",
        apiStatus: "online",
      });

      const composer = screen.getByRole("textbox");
      fireEvent.change(composer, {
        target: { value: "%re", selectionStart: 3, selectionEnd: 3 },
      });

      expect(await screen.findByRole("option", { name: /remember/i })).toBeInTheDocument();
      const initiallyActive = screen
        .getAllByRole("option")
        .find((option) => option.getAttribute("aria-selected") === "true");
      const baselineCalls = scrollSpy.mock.calls.length;

      fireEvent.keyDown(composer, { key: "ArrowDown" });

      await waitFor(() => {
        expect(scrollSpy.mock.calls.length).toBeGreaterThan(baselineCalls);
      });
      await waitFor(() => {
        const activeOption = screen
          .getAllByRole("option")
          .find((option) => option.getAttribute("aria-selected") === "true");
        expect(activeOption).toBeTruthy();
        expect(activeOption).not.toBe(initiallyActive);
        expect(activeOption).toHaveClass("is-active");
      });
    } finally {
      getSpy.mockRestore();
      scrollSpy.mockRestore();
      if (restoreScrollIntoView) {
        delete Element.prototype.scrollIntoView;
      }
    }
  });

  it("renders the chat settings popover above the composer stack", async () => {
    renderChat({
      sessionId: "sess-chat-settings",
      apiStatus: "online",
    });

    fireEvent.click(screen.getAllByRole("button", { name: /chat settings/i })[0]);

    await waitFor(() => {
      expect(document.querySelector(".chat-settings-popover")).not.toBeNull();
    });

    expect(document.querySelector(".input-box .chat-settings-popover")).toBeNull();
  });

  it("opens the agent console when inline tool links use console behavior", () => {
    const onOpenConsole = vi.fn();
    renderChat(
      {
        sessionId: "sess-tool-console",
        toolDisplayMode: "console",
        toolLinkBehavior: "console",
        conversation: [
          {
            role: "ai",
            id: "ai-tool",
            text: "Used [[tool_call:0]] to search.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "otters" } }),
              ],
            },
            tools: [
              {
                id: "tool-1",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Otter result"}}',
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Used search." }],
      },
      { onOpenConsole },
    );

    fireEvent.click(screen.getByLabelText("Open search_web"));

    expect(onOpenConsole).toHaveBeenCalledWith({
      toolId: "tool-1",
      chainId: "ai-tool",
    });
  });

  it("falls back to the agent console when inline links are preferred but tool cards stay in the console", () => {
    const onOpenConsole = vi.fn();
    renderChat(
      {
        sessionId: "sess-tool-console-fallback",
        toolDisplayMode: "console",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-fallback",
            text: "Used [[tool_call:0]] to search.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "otters" } }),
              ],
            },
            tools: [
              {
                id: "tool-1",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Otter result"}}',
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Used search." }],
      },
      { onOpenConsole },
    );

    fireEvent.click(screen.getByLabelText("Open search_web"));

    expect(onOpenConsole).toHaveBeenCalledWith({
      toolId: "tool-1",
      chainId: "ai-tool-fallback",
    });
    expect(screen.queryByText("show tools (1)")).not.toBeInTheDocument();
  });

  it("expands inline tool cards and unwraps JSON payloads for inline tool links", async () => {
    const onOpenConsole = vi.fn();
    renderChat(
      {
        sessionId: "sess-tool-inline",
        toolDisplayMode: "inline",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-inline",
            text: "Used [[tool_call:0]] to search.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "otters" } }),
              ],
            },
            tools: [
              {
                id: "tool-1",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Otter result"}}',
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Used search." }],
      },
      {
        activeMessageId: "ai-tool-inline",
        setActiveMessageId: vi.fn(),
        onOpenConsole,
      },
    );

    expect(screen.getByText("show tools (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Open search_web"));

    await waitFor(() => {
      expect(screen.getByText("hide tools")).toBeInTheDocument();
    });

    expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
    openFirstInlineToolCard();
    await waitFor(() => {
      expect(getFirstInlineToolCard()).toHaveAttribute("open");
    });

    const result = await screen.findByLabelText("Tool result");
    expect(result).toHaveTextContent('"title": "Otter result"');
    expect(result).not.toHaveTextContent('"status": "ok"');
    expect(onOpenConsole).not.toHaveBeenCalled();
  });

  it("keeps inline tool cards visible in both mode", async () => {
    const onOpenConsole = vi.fn();
    renderChat(
      {
        sessionId: "sess-tool-both",
        toolDisplayMode: "both",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-both",
            text: "Used [[tool_call:0]] to search.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "otters" } }),
              ],
            },
            tools: [
              {
                id: "tool-1",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Otter result"}}',
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Used search." }],
      },
      {
        activeMessageId: "ai-tool-both",
        setActiveMessageId: vi.fn(),
        onOpenConsole,
      },
    );

    expect(screen.getByText("show tools (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Open search_web"));

    await waitFor(() => {
      expect(screen.getByText("hide tools")).toBeInTheDocument();
    });

    expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
    openFirstInlineToolCard();
    await waitFor(() => {
      expect(getFirstInlineToolCard()).toHaveAttribute("open");
    });
    expect(await screen.findByLabelText("Tool result")).toHaveTextContent(
      '"title": "Otter result"',
    );
    expect(onOpenConsole).not.toHaveBeenCalled();
  });

  it("only shows inline tools for the selected message in auto mode", () => {
    renderChat(
      {
        sessionId: "sess-tool-auto",
        toolDisplayMode: "auto",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-auto-1",
            text: "First [[tool_call:0]] tool.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "otters" } }),
              ],
            },
            tools: [
              {
                id: "tool-auto-1",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Otter result"}}',
              },
            ],
          },
          {
            role: "ai",
            id: "ai-tool-auto-2",
            text: "Second [[tool_call:0]] tool.",
            timestamp: "2024-01-01T00:00:02Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "search_web", params: { query: "badgers" } }),
              ],
            },
            tools: [
              {
                id: "tool-auto-2",
                name: "search_web",
                args: { query: "badgers" },
                status: "invoked",
                result: '{"status":"ok","data":{"title":"Badger result"}}',
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Used tools." }],
      },
      {
        activeMessageId: "ai-tool-auto-2",
        setActiveMessageId: vi.fn(),
      },
    );

    expect(screen.getAllByText("show tools (1)")).toHaveLength(1);
    expect(screen.queryByText("Badger result")).not.toBeInTheDocument();
  });

  it("renders computer tool results inline without leaking raw JSON payloads", async () => {
    const onOpenConsole = vi.fn();
    renderChat(
      {
        sessionId: "sess-tool-computer-inline",
        toolDisplayMode: "inline",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-computer-inline",
            text: "Observed [[tool_call:0]] before clicking.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "computer.observe", params: { session_id: "sess-computer-1" } }),
              ],
            },
            tools: [
              {
                id: "tool-computer-1",
                name: "computer.observe",
                args: { session_id: "sess-computer-1" },
                status: "invoked",
                result: JSON.stringify({
                  status: "invoked",
                  ok: true,
                  data: {
                    summary: "Captured browser state",
                    current_url: "https://example.com",
                    active_window: "Example Domain",
                    attachment: {
                      url: "https://example.com/screenshot.png",
                      name: "screenshot.png",
                    },
                  },
                }),
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Observed the page." }],
      },
      {
        activeMessageId: "ai-tool-computer-inline",
        setActiveMessageId: vi.fn(),
        onOpenConsole,
      },
    );

    fireEvent.click(screen.getByLabelText("Open computer.observe"));

    await waitFor(() => {
      expect(screen.getByText("hide tools")).toBeInTheDocument();
    });

    expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
    openFirstInlineToolCard();
    await waitFor(() => {
      expect(getFirstInlineToolCard()).toHaveAttribute("open");
    });

    const result = await screen.findByLabelText("Tool result");
    expect(result).toHaveTextContent("Captured browser state");
    expect(result).toHaveTextContent("https://example.com");
    expect(result).toHaveTextContent("Example Domain");
    expect(screen.getByAltText("screenshot.png")).toBeInTheDocument();
    expect(result).not.toHaveTextContent('"current_url": "https://example.com"');
    expect(result).not.toHaveTextContent('"attachment"');
    expect(onOpenConsole).not.toHaveBeenCalled();
  });

  it("renders camera capture results inline with the embedded image", async () => {
    renderChat(
      {
        sessionId: "sess-tool-camera-inline",
        toolDisplayMode: "inline",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-camera-inline",
            text: "Captured [[tool_call:0]] for you.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "camera.capture", params: {} }),
              ],
            },
            tools: [
              {
                id: "tool-camera-1",
                name: "camera.capture",
                args: {},
                status: "invoked",
                result: JSON.stringify({
                  status: "invoked",
                  ok: true,
                  data: {
                    capture_id: "capture-inline-1",
                    filename: "selfie.png",
                    source: "camera",
                    attachment: {
                      url: "/api/captures/capture-inline-1/content",
                      name: "selfie.png",
                      capture_id: "capture-inline-1",
                    },
                  },
                }),
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Captured the camera frame." }],
      },
      {
        activeMessageId: "ai-tool-camera-inline",
        setActiveMessageId: vi.fn(),
      },
    );

    fireEvent.click(screen.getByLabelText("Open camera.capture"));

    await waitFor(() => {
      expect(screen.getByText("hide tools")).toBeInTheDocument();
    });

    expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
    openFirstInlineToolCard();
    await waitFor(() => {
      expect(getFirstInlineToolCard()).toHaveAttribute("open");
    });

    const result = await screen.findByLabelText("Tool result");
    expect(screen.getByAltText("selfie.png")).toBeInTheDocument();
    expect(result).toHaveTextContent("selfie.png");
    expect(result).toHaveTextContent("capture-inline-1");
    expect(result).not.toHaveTextContent('"attachment"');
    expect(result).not.toHaveTextContent('"capture_id": "capture-inline-1"');
  });

  it("opens the browser popup from inline tool cards and refreshes via computer.observe", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/tools/invoke") {
        return Promise.resolve({
          data: {
            result: {
              status: "invoked",
              ok: true,
              data: {
                summary: "Refreshed browser state",
                session: {
                  id: "browser-session-inline-1",
                  runtime: "browser",
                  width: 1280,
                  height: 720,
                },
                attachment: {
                  url: "/api/captures/capture-inline-2/content",
                  name: "capture-inline-2.png",
                },
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat(
        {
          sessionId: "sess-tool-browser-inline",
          toolDisplayMode: "both",
          toolLinkBehavior: "inline",
          conversation: [
            {
              role: "ai",
              id: "ai-tool-browser-inline",
              text: "Observed [[tool_call:0]] before clicking.",
              timestamp: "2024-01-01T00:00:01Z",
              metadata: {
                inline_tool_payloads: [
                  JSON.stringify({
                    tool: "computer.observe",
                    params: { session_id: "browser-session-inline-1" },
                  }),
                ],
              },
              tools: [
                {
                  id: "tool-browser-inline-1",
                  name: "computer.observe",
                  args: { session_id: "browser-session-inline-1" },
                  status: "invoked",
                  result: JSON.stringify({
                    status: "invoked",
                    ok: true,
                    data: {
                      summary: "Captured browser state",
                      current_url: "https://example.com",
                      session: {
                        id: "browser-session-inline-1",
                        runtime: "browser",
                        width: 1280,
                        height: 720,
                      },
                      attachment: {
                        url: "/api/captures/capture-inline-1/content",
                        name: "capture-inline-1.png",
                      },
                    },
                  }),
                },
              ],
            },
          ],
          history: [{ role: "ai", text: "Observed the page." }],
        },
        {
          activeMessageId: "ai-tool-browser-inline",
          setActiveMessageId: vi.fn(),
        },
      );

      fireEvent.click(screen.getByLabelText("Open computer.observe"));

      await waitFor(() => {
        expect(screen.getByText("hide tools")).toBeInTheDocument();
      });

      expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
      openFirstInlineToolCard();
      await waitFor(() => {
        expect(getFirstInlineToolCard()).toHaveAttribute("open");
      });
      fireEvent.click(screen.getByRole("button", { name: /expand browser/i }));

      const dialog = await screen.findByRole("dialog", {
        name: /browser session controls/i,
      });
      expect(within(dialog).getByDisplayValue("https://example.com")).toBeInTheDocument();
      expect(within(dialog).getByAltText("capture-inline-1.png")).toBeInTheDocument();

      fireEvent.click(within(dialog).getByRole("button", { name: /^refresh$/i }));

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledWith(
          "/api/tools/invoke",
          expect.objectContaining({
            name: "computer.observe",
            args: { session_id: "browser-session-inline-1" },
            message_id: "ai-tool-browser-inline",
            chain_id: "ai-tool-browser-inline",
            session_id: "sess-tool-browser-inline",
          }),
        );
      });
    } finally {
      postSpy.mockRestore();
    }
  });

  it("treats wrapped tool failures as resolved instead of leaving approval buttons visible", async () => {
    renderChat(
      {
        sessionId: "sess-tool-error-inline",
        toolDisplayMode: "inline",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-error-inline",
            text: "Tried [[tool_call:0]] before the approval failed.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "computer.act", params: { session_id: "sess-computer-1" } }),
              ],
            },
            tools: [
              {
                id: "tool-error-1",
                name: "computer.act",
                args: { session_id: "sess-computer-1" },
                status: "proposed",
                result: JSON.stringify({
                  status: "error",
                  ok: false,
                  message: "Approval missing.",
                }),
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Tool failed." }],
      },
      {
        activeMessageId: "ai-tool-error-inline",
        setActiveMessageId: vi.fn(),
      },
    );

    fireEvent.click(screen.getByText("show tools (1)"));

    await waitFor(() => {
      expect(screen.getByText("hide tools")).toBeInTheDocument();
    });

    expect(getFirstInlineToolCard()).not.toHaveAttribute("open");
    openFirstInlineToolCard();
    await waitFor(() => {
      expect(getFirstInlineToolCard()).toHaveAttribute("open");
    });
    expect(await screen.findByLabelText("Tool result")).toHaveTextContent(
      "Approval missing.",
    );
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
  });

  it("reopens chat input and focuses the composer after a new-chat event", async () => {
    const originalRaf = globalThis.requestAnimationFrame;
    const focusSpy = vi
      .spyOn(HTMLTextAreaElement.prototype, "focus")
      .mockImplementation(() => {});
    const rafQueue = [];
    globalThis.requestAnimationFrame = (callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    };

    try {
      renderChat({
        sessionId: "sess-focus",
        conversation: [
          { role: "user", text: "Hi", timestamp: "2024-01-01T00:00:00Z" },
        ],
        history: [{ role: "user", text: "Hi" }],
      });

      fireEvent.click(screen.getByLabelText("Close chat input"));
      expect(screen.queryByPlaceholderText("Type your message...")).not.toBeInTheDocument();

      await act(async () => {
        window.dispatchEvent(new Event("float:new-chat"));
      });

      await waitFor(() => {
        expect(screen.getByPlaceholderText("Type your message...")).toBeInTheDocument();
      });

      await act(async () => {
        while (rafQueue.length) {
          const next = rafQueue.shift();
          next?.(0);
        }
      });

      await waitFor(() => {
        expect(focusSpy).toHaveBeenCalled();
      });
    } finally {
      globalThis.requestAnimationFrame = originalRaf;
      focusSpy.mockRestore();
    }
  });

  it("shows the live overlay while connecting and lets stop cancel before connect completes", async () => {
    const connectPromise = new Promise(() => {});
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/voice/connect") {
        return connectPromise;
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-live-connect",
        apiStatus: "online",
      });

      const liveButton = screen.getAllByRole("button", {
        name: /live streaming mode/i,
      })[0];

      fireEvent.click(liveButton);

      await waitFor(() => {
        expect(screen.getByText("live streaming mode")).toBeInTheDocument();
        expect(screen.getAllByText("connecting").length).toBeGreaterThan(0);
      });

      fireEvent.click(liveButton);

      await waitFor(() => {
        expect(screen.queryByText("live streaming mode")).not.toBeInTheDocument();
      });

      expect(postSpy).toHaveBeenCalledTimes(1);
    } finally {
      postSpy.mockRestore();
    }
  });
});
