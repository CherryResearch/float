import React from "react";
import { vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";
import { GlobalContext } from "../../main";
import Chat, { formatMessageTimestampLabel } from "../Chat";

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

    const result = screen.getByLabelText("Tool result");
    expect(result).toHaveTextContent('"title": "Otter result"');
    expect(result).not.toHaveTextContent('"status": "ok"');
    expect(onOpenConsole).not.toHaveBeenCalled();
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
