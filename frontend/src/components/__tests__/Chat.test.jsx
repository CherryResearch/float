import React from "react";
import { afterEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";
import { GlobalContext } from "../../main";
import { CHAT_WINDOW_STORAGE_KEY } from "../../utils/chatWindowSizing";
import { TOOL_REVIEW_ACTION_EVENT } from "../../utils/toolReviewActions";
import Chat, {
  buildCommandAwareRequest,
  formatMessageTimestampLabel,
  mergeAssistantMessageMetadata,
  mergeToolEntries,
  prepareComposerSubmission,
  resolveRegenerateRequestTarget,
  resolveSubchatControlFromTools,
} from "../Chat";

describe("Chat", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--center-rail-width");
    localStorage.clear();
  });

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

  it("shows transparent compaction controls for trimmed long conversations", () => {
    renderChat({
      conversation: [
        { role: "user", text: "Tail message 1" },
        { role: "ai", text: "Tail message 2" },
        { role: "user", text: "Tail message 3" },
      ],
      conversationTrimMeta: {
        truncated: true,
        total_messages: 10,
        omitted_messages: 7,
        start_index: 7,
        message_limit: 3,
      },
    });

    expect(screen.getByText("Full transcript saved.")).toBeInTheDocument();
    expect(
      screen.getByText(/Rendering latest 3 of 10 messages\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByText("7 earlier messages stay out of the DOM."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /preview compaction/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create compacted copy/i }),
    ).toBeInTheDocument();
  });

  it("does not suggest context compaction for tool-only trim metadata", () => {
    renderChat({
      conversation: [
        { role: "user", text: "Message 1" },
        { role: "ai", text: "Message 2" },
      ],
      conversationTrimMeta: {
        truncated: true,
        total_messages: 2,
        omitted_messages: 0,
        start_index: 0,
        message_limit: 80,
        omitted_tools: 12,
        tool_limit: 40,
      },
    });

    expect(screen.queryByText("Full transcript saved.")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /preview compaction/i }),
    ).not.toBeInTheDocument();
  });

  it("previews conversation compaction with deterministic mode by default", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/conversations/compact/preview") {
        return Promise.resolve({
          data: {
            summary_preview: "Condensed context for continued work.",
            summary_method: "deterministic",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-long",
        conversation: [
          { role: "user", text: "Tail message 1" },
          { role: "ai", text: "Tail message 2" },
          { role: "user", text: "Tail message 3" },
        ],
        conversationTrimMeta: {
          truncated: true,
          total_messages: 10,
          omitted_messages: 7,
          start_index: 7,
          message_limit: 3,
        },
      });

      fireEvent.click(screen.getByRole("button", { name: /preview compaction/i }));

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledWith("/api/conversations/compact/preview", {
          conversation_id: "sess-long",
          keep_last: 3,
          max_summary_chars: 6000,
          summary_mode: "deterministic",
        });
      });
      expect(
        await screen.findByText("Condensed context for continued work."),
      ).toBeInTheDocument();
      expect(screen.getByText("Preview ready (deterministic).")).toBeInTheDocument();
    } finally {
      postSpy.mockRestore();
    }
  });

  it("shows live compaction progress while a preview request is pending", async () => {
    let resolvePreview;
    const previewPromise = new Promise((resolve) => {
      resolvePreview = resolve;
    });
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/conversations/compact/preview") {
        return previewPromise;
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-long",
        conversation: [
          { role: "user", text: "Tail message 1" },
          { role: "ai", text: "Tail message 2" },
          { role: "user", text: "Tail message 3" },
        ],
        conversationTrimMeta: {
          truncated: true,
          total_messages: 10,
          omitted_messages: 7,
          start_index: 7,
          message_limit: 3,
        },
      });

      fireEvent.click(screen.getByRole("button", { name: /preview compaction/i }));

      expect(
        await screen.findByText(/Previewing compacted conversation context/i),
      ).toBeInTheDocument();

      await act(async () => {
        resolvePreview({
          data: {
            summary_preview: "Condensed context for continued work.",
            summary_method: "deterministic",
            elapsed_ms: 1200,
          },
        });
      });

      expect(await screen.findByText("Preview ready (deterministic).")).toBeInTheDocument();
      expect(screen.getByText(/elapsed 1\.2 s/i)).toBeInTheDocument();
    } finally {
      postSpy.mockRestore();
    }
  });

  it("creates a compacted conversation copy from the long chat notice", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/conversations/compact/write") {
        return Promise.resolve({
          data: { target_conversation_id: "sess-long-compacted" },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-long",
        conversation: [
          { role: "user", text: "Tail message 1" },
          { role: "ai", text: "Tail message 2" },
          { role: "user", text: "Tail message 3" },
        ],
        conversationTrimMeta: {
          truncated: true,
          total_messages: 10,
          omitted_messages: 7,
          start_index: 7,
          message_limit: 3,
        },
      });

      fireEvent.click(screen.getByRole("button", { name: /create compacted copy/i }));

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledWith("/api/conversations/compact/write", {
          conversation_id: "sess-long",
          keep_last: 3,
          max_summary_chars: 6000,
          summary_mode: "deterministic",
          target_conversation_id: "",
          replace: false,
        });
      });
      expect(
        screen.getByText("Created compacted copy: sess-long-compacted."),
      ).toBeInTheDocument();
    } finally {
      postSpy.mockRestore();
    }
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

  it("dedupes inline metadata tools when a real tool result already exists", () => {
    const merged = mergeToolEntries(
      [
        {
          id: "task-1",
          name: "create_task",
          args: {
            title: "Follow up on meetup",
            start_time: 1774241400,
            timezone: "UTC",
            status: "pending",
          },
          status: "invoked",
        },
      ],
      [],
      {
        inline_tool_payloads: [
          JSON.stringify({
            tool: "create_task",
            args: {
              title: "Follow up on meetup",
              start_time: 1774241400,
              timezone: "UTC",
              status: "pending",
              notes: ["Ask how it went."],
            },
          }),
        ],
      },
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].id).toBe("task-1");
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

  it("continues the resolved part of a notification-selected tool batch", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/chat/continue") {
        return Promise.resolve({
          data: { message: "Continued after the ready tool.", metadata: {}, tools_used: [] },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-batch-continue",
        apiModel: "gpt-5.4",
        conversation: [
          {
            role: "ai",
            id: "msg-batch",
            text: "I need these tools.",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: { tool_response_pending: true },
            tools: [
              {
                id: "tool-ready",
                name: "search_web",
                args: { query: "otters" },
                status: "invoked",
                result: { status: "invoked", ok: true, data: { title: "Otters" } },
              },
              {
                id: "tool-pending",
                name: "list_dir",
                args: { path: "." },
                status: "proposed",
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "I need these tools." }],
      });

      await act(async () => {
        window.dispatchEvent(
          new CustomEvent(TOOL_REVIEW_ACTION_EVENT, {
            detail: {
              action: "continue",
              scope: "batch",
              toolIds: ["tool-ready", "tool-pending"],
              messageId: "msg-batch",
              chainId: "msg-batch",
              handled: false,
            },
          }),
        );
        await Promise.resolve();
      });

      await waitFor(() => {
        const call = postSpy.mock.calls.find(([url]) => url === "/api/chat/continue");
        expect(call).toBeTruthy();
        expect(call[1]).toEqual(
          expect.objectContaining({
            session_id: "sess-batch-continue",
            message_id: "msg-batch",
            tools: [expect.objectContaining({ id: "tool-ready" })],
          }),
        );
      });
    } finally {
      postSpy.mockRestore();
    }
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

  it("marks live realtime assistant bubbles in the source label", () => {
    renderChat({
      conversation: [
        {
          role: "ai",
          id: "live-provider-msg",
          text: "Realtime reply.",
          timestamp: "2024-01-01T00:00:01Z",
          metadata: {
            live_stream: {
              source: "realtime",
              mode: "api",
              model: "gpt-realtime",
              provider: "openai-realtime",
              session_id: "sess-live-provider",
            },
          },
        },
      ],
      history: [{ role: "ai", text: "Realtime reply." }],
    });

    expect(screen.getByText("live/api:gpt-realtime")).toBeInTheDocument();
  });

  it("persists chat window width changes from the resize handle keyboard controls", () => {
    renderChat();

    const chatContainer = document.querySelector(".chat-container");
    expect(chatContainer).not.toBeNull();
    Object.defineProperty(chatContainer, "getBoundingClientRect", {
      configurable: true,
      value: () => ({
        width: 900,
        height: 640,
        top: 0,
        left: 0,
        right: 900,
        bottom: 640,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });

    const handle = screen.getByRole("separator", { name: /resize chat window/i });
    fireEvent.keyDown(handle, { key: "ArrowRight" });

    expect(document.documentElement.style.getPropertyValue("--center-rail-width")).toBe(
      "948px",
    );
    expect(localStorage.getItem(CHAT_WINDOW_STORAGE_KEY)).toBe("948");

    fireEvent.keyDown(handle, { key: "Home" });

    expect(document.documentElement.style.getPropertyValue("--center-rail-width")).toBe("");
    expect(localStorage.getItem(CHAT_WINDOW_STORAGE_KEY)).toBeNull();
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

  it("does not log disabled Tooltip child warnings when sending with assistant actions present", async () => {
    let resolveChat;
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/devices/register") {
        return Promise.resolve({ data: { device: { id: "device-test" } } });
      }
      if (url === "/api/devices/token") {
        return Promise.resolve({ data: { token: "token-test" } });
      }
      if (url === "/api/chat") {
        return new Promise((resolve) => {
          resolveChat = resolve;
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-tooltip-disabled-actions",
        apiStatus: "online",
        conversation: [
          { role: "user", text: "Earlier prompt", timestamp: "2024-01-01T00:00:00Z" },
          {
            role: "ai",
            id: "assistant-with-actions",
            text: "Earlier answer",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              unresolved_tool_loop: true,
            },
          },
        ],
        history: [
          { role: "user", text: "Earlier prompt" },
          { role: "ai", text: "Earlier answer" },
        ],
      });

      await act(async () => {
        fireEvent.change(screen.getByRole("textbox"), {
          target: { value: "Trigger loading state" },
        });
        fireEvent.click(screen.getAllByRole("button", { name: /send message/i })[0]);
        await Promise.resolve();
      });

      await waitFor(() => {
        const chatCall = postSpy.mock.calls.find(([url]) => url === "/api/chat");
        expect(chatCall).toBeTruthy();
      });

      const tooltipWarnings = consoleErrorSpy.mock.calls
        .map((args) => args.join(" "))
        .filter((message) =>
          message.includes(
            "MUI: You are providing a disabled `button` child to the Tooltip component.",
          ),
        );
      expect(tooltipWarnings).toHaveLength(0);

      await act(async () => {
        resolveChat?.({
          data: {
            message: "Done",
            metadata: {},
            tools_used: [],
          },
        });
        await Promise.resolve();
      });
    } finally {
      postSpy.mockRestore();
      consoleErrorSpy.mockRestore();
      localStorage.clear();
    }
  });

  it("plays assistant TTS once and toggles pause/resume on repeated clicks", async () => {
    const audioInstances = [];
    class MockAudio {
      constructor(src) {
        this.src = src;
        this.currentTime = 0;
        this.duration = 42;
        this.paused = true;
        this.volume = 1;
        this.play = vi.fn(async () => {
          this.paused = false;
          this.onloadedmetadata?.();
          this.onplay?.();
          this.ontimeupdate?.();
        });
        this.pause = vi.fn(() => {
          this.paused = true;
          this.onpause?.();
        });
        audioInstances.push(this);
      }
    }
    const originalAudio = globalThis.Audio;
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/voice/tts") {
        return Promise.resolve({
          data: {
            audio_b64: "ZmFrZS1hdWRpbw==",
            content_type: "audio/wav",
            provider: "openai",
            model: "tts-1-hd",
            voice: "nova",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    Object.defineProperty(globalThis, "Audio", {
      configurable: true,
      writable: true,
      value: MockAudio,
    });

    try {
      renderChat({
        sessionId: "sess-tts-playback",
        ttsModel: "tts-1-hd",
        voiceModel: "nova",
        conversation: [
          { role: "user", text: "Tell me something.", timestamp: "2024-01-01T00:00:00Z" },
          {
            role: "ai",
            id: "assistant-tts",
            text: "Here is the response to speak.",
            timestamp: "2024-01-01T00:00:01Z",
          },
        ],
        history: [
          { role: "user", text: "Tell me something." },
          { role: "ai", text: "Here is the response to speak." },
        ],
      });

      const speakButton = screen.getByLabelText("Speak assistant response");
      expect(speakButton.getAttribute("title")).toContain(
        "Text-to-speech route: API (OpenAI)",
      );
      expect(speakButton.getAttribute("title")).toContain("model: tts-1-hd");
      expect(speakButton.getAttribute("title")).toContain("voice: nova");
      fireEvent.click(speakButton);

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledTimes(1);
      });
      expect(postSpy.mock.calls[0][1]).toMatchObject({
        model: "tts-1-hd",
        voice: "nova",
      });
      await waitFor(() => {
        expect(audioInstances).toHaveLength(1);
        expect(audioInstances[0].play).toHaveBeenCalledTimes(1);
      });
      await waitFor(() => {
        expect(document.querySelector(".tts-progress")).toBeInTheDocument();
      });
      expect(document.querySelector(".tts-progress").getAttribute("title")).toContain(
        "Text-to-speech route: API (OpenAI) | model: tts-1-hd | voice: nova",
      );

      fireEvent.click(speakButton);

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledTimes(1);
        expect(audioInstances[0].pause).toHaveBeenCalledTimes(1);
      });

      fireEvent.click(speakButton);

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledTimes(1);
        expect(audioInstances[0].play).toHaveBeenCalledTimes(2);
      });
      expect(screen.getByLabelText("Speak assistant response")).toBeInTheDocument();
    } finally {
      postSpy.mockRestore();
      Object.defineProperty(globalThis, "Audio", {
        configurable: true,
        writable: true,
        value: originalAudio,
      });
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

  it("recognizes a trailing percent tool command as a tool directive", () => {
    const result = buildCommandAwareRequest(
      "well youre requesting the same 8 tools you have context for all 39 %tool_help",
    );

    expect(result.toolDirective).toEqual(
      expect.objectContaining({
        toolName: "tool_help",
        body: "well youre requesting the same 8 tools you have context for all 39",
      }),
    );
    expect(result.requestMessage).toContain("prefer the `tool_help` tool");
    expect(result.requestMessage).not.toContain("%tool_help");
  });

  it("keeps leading percent tool commands as focused tool directives", () => {
    const result = buildCommandAwareRequest("%tool_help computer");

    expect(result.toolDirective).toEqual(
      expect.objectContaining({
        toolName: "tool_help",
        body: "computer",
      }),
    );
    expect(result.requestMessage).toContain("computer");
    expect(result.requestMessage).toContain("prefer the `tool_help` tool");
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

  it("renders subchat links in the message footer and opens the child conversation", () => {
    const onOpenConversation = vi.fn();
    renderChat(
      {
        sessionId: "sess-subchat-parent",
        conversation: [
          {
            role: "ai",
            id: "ai-parent",
            text: "I started a background branch.",
            timestamp: "2024-01-01T00:00:01Z",
          },
        ],
        history: [{ role: "ai", text: "I started a background branch." }],
      },
      {
        onOpenConversation,
        subchatLinksByMessage: {
          "ai-parent": [
            {
              conversationId: "task-background-plan",
              label: "Background: plan",
              kind: "subchat",
              messageCount: 2,
            },
          ],
        },
      },
    );

    expect(screen.getByText("subchats (1)")).toBeInTheDocument();
    expect(screen.getByText("2 messages")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /open subchat background: plan/i }),
    );

    expect(onOpenConversation).toHaveBeenCalledWith(
      "task-background-plan",
      "Background: plan",
    );
  });

  it("renders a light return button inside subchats", () => {
    const onOpenConversation = vi.fn();
    renderChat(
      {
        sessionId: "task-child",
        conversation: [
          {
            role: "ai",
            id: "child-ai",
            text: "Working inside a subchat.",
            timestamp: "2024-01-01T00:00:01Z",
          },
        ],
      },
      {
        onOpenConversation,
        parentConversationLink: {
          conversationId: "sess-parent",
          label: "Main planning chat",
        },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: /back to main chat/i }));

    expect(onOpenConversation).toHaveBeenCalledWith(
      "sess-parent",
      "Main planning chat",
    );
  });

  it("extracts subchat control payloads from invoked tool results", () => {
    const control = resolveSubchatControlFromTools([
      {
        name: "subchat",
        result: {
          status: "invoked",
          ok: true,
          data: {
            status: "ok",
            action: "return",
            control: {
              kind: "subchat_control",
              action: "return_to_parent",
              parent_session_id: "sess-parent",
              parent_message_id: "ai-parent",
            },
          },
        },
      },
    ]);

    expect(control).toMatchObject({
      action: "return_to_parent",
      parentSessionId: "sess-parent",
      parentMessageId: "ai-parent",
    });

    expect(
      resolveSubchatControlFromTools([
        {
          name: "subchat",
          result: {
            data: {
              control: {
                kind: "subchat_control",
                action: "continue",
                requested_minutes: 15,
              },
            },
          },
        },
      ]),
    ).toMatchObject({ action: "continue", requestedMinutes: 15 });
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
    expect(result).toHaveTextContent("title");
    expect(result).toHaveTextContent("Otter result");
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
    const result = await screen.findByLabelText("Tool result");
    expect(result).toHaveTextContent("title");
    expect(result).toHaveTextContent("Otter result");
    expect(onOpenConsole).not.toHaveBeenCalled();
  });

  it("shows actionable inline tool controls by default in both mode", () => {
    renderChat(
      {
        sessionId: "sess-tool-both-pending",
        toolDisplayMode: "both",
        toolLinkBehavior: "inline",
        conversation: [
          {
            role: "ai",
            id: "ai-tool-both-pending",
            text: "Requested [[tool_call:0]].",
            timestamp: "2024-01-01T00:00:01Z",
            metadata: {
              inline_tool_payloads: [
                JSON.stringify({ tool: "tool_help", params: { topic: "memory" } }),
              ],
            },
            tools: [
              {
                id: "tool-pending-1",
                name: "tool_help",
                args: { topic: "memory" },
                status: "pending",
              },
            ],
          },
        ],
        history: [{ role: "ai", text: "Requested tool help." }],
      },
      {
        activeMessageId: "ai-tool-both-pending",
        setActiveMessageId: vi.fn(),
      },
    );

    expect(screen.getByText("hide tools")).toBeInTheDocument();
    expect(screen.queryByText("show tools (1)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();

    const getSpy = vi.spyOn(axios, "get").mockResolvedValue({ data: { tools: [] } });
    try {
      fireEvent.click(screen.getByRole("button", { name: /edit & retry/i }));
      expect(
        await screen.findByRole("dialog", { name: /computer\.act/i }),
      ).toBeInTheDocument();
    } finally {
      getSpy.mockRestore();
    }
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

  it("allows live streaming startup while the normal chat lane is server", async () => {
    const connectPromise = new Promise(() => {});
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/voice/connect") {
        return connectPromise;
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-live-server-connect",
        backendMode: "server",
      });

      fireEvent.click(
        screen.getAllByRole("button", {
          name: /live streaming mode/i,
        })[0],
      );

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledWith(
          "/api/voice/connect",
          expect.objectContaining({
            room: "float",
          }),
        );
        expect(screen.getByText("live streaming mode")).toBeInTheDocument();
      });
    } finally {
      postSpy.mockRestore();
    }
  });

  it("surfaces a local bridge warning when the backend returns the local live transport", async () => {
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/voice/connect") {
        return Promise.resolve({
          data: {
            provider: "float-local-live",
            transport: "local-bridge",
            detail:
              "Local live bridge is selected, but browser duplex audio is not wired yet.",
            mode: "local",
            model: "gemma-4-E2B-it",
            multimodal_model: "gemma-4-E4B-it",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    try {
      renderChat({
        sessionId: "sess-local-live-connect",
      });

      fireEvent.click(
        screen.getAllByRole("button", {
          name: /live streaming mode/i,
        })[0],
      );

      await waitFor(() => {
        expect(postSpy).toHaveBeenCalledWith(
          "/api/voice/connect",
          expect.objectContaining({
            room: "float",
          }),
        );
        expect(screen.getByText("Live streaming mode failed")).toBeInTheDocument();
        expect(
          screen.getAllByText(
            /local live bridge is selected, but browser duplex audio is not wired yet/i,
          ),
        ).not.toHaveLength(0);
      });
    } finally {
      postSpy.mockRestore();
    }
  });

  it("waits for realtime tool configuration before requesting the first assistant response", async () => {
    const originalFetch = global.fetch;
    const originalPeerConnection = globalThis.RTCPeerConnection;
    const originalWebkitPeerConnection = globalThis.webkitRTCPeerConnection;
    const originalMediaDevices = navigator.mediaDevices;
    const originalMediaPause = globalThis.HTMLMediaElement?.prototype.pause;
    const originalMediaPlay = globalThis.HTMLMediaElement?.prototype.play;
    const originalAudioPause = globalThis.HTMLAudioElement?.prototype.pause;
    const originalAudioPlay = globalThis.HTMLAudioElement?.prototype.play;
    const sentRealtimeEvents = [];
    let activeDataChannel = null;
    let resolveToolSpecs;
    const toolSpecsPromise = new Promise((resolve) => {
      resolveToolSpecs = resolve;
    });
    const mockTrack = {
      stop: vi.fn(),
      addEventListener: vi.fn(),
    };
    let view = null;

    class MockDataChannel {
      constructor() {
        this.readyState = "open";
        this.listeners = {};
      }

      addEventListener(type, callback) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(callback);
      }

      send(payload) {
        sentRealtimeEvents.push(JSON.parse(payload));
      }

      close() {
        this.readyState = "closed";
        this.emit("close");
      }

      emit(type, data) {
        (this.listeners[type] || []).forEach((callback) => {
          if (type === "message") {
            callback({ data });
            return;
          }
          callback({ type });
        });
      }
    }

    class MockPeerConnection {
      constructor() {
        activeDataChannel = new MockDataChannel();
        this.connectionState = "new";
      }

      addTransceiver() {
        return {
          sender: {
            replaceTrack: vi.fn(),
          },
        };
      }

      addTrack() {}

      createDataChannel() {
        return activeDataChannel;
      }

      async createOffer() {
        return { sdp: "offer-sdp" };
      }

      async setLocalDescription(description) {
        this.localDescription = description;
      }

      async setRemoteDescription(description) {
        this.remoteDescription = description;
        activeDataChannel?.emit("open");
      }

      close() {
        this.connectionState = "closed";
        activeDataChannel?.close();
      }
    }

    const getSpy = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/tools/specs") {
        return toolSpecsPromise;
      }
      return Promise.resolve({ data: {} });
    });
    const postSpy = vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/voice/connect") {
        return Promise.resolve({
          data: {
            provider: "openai-realtime",
            client_secret: "ephemeral-secret",
            url: "https://example.test/realtime",
            session_id: "sess-live-tool-config",
            model: "gpt-realtime",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    Object.defineProperty(globalThis, "RTCPeerConnection", {
      configurable: true,
      writable: true,
      value: MockPeerConnection,
    });
    Object.defineProperty(globalThis, "webkitRTCPeerConnection", {
      configurable: true,
      writable: true,
      value: undefined,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [mockTrack],
          getAudioTracks: () => [mockTrack],
        }),
      },
    });
    Object.defineProperty(globalThis.HTMLMediaElement.prototype, "pause", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    Object.defineProperty(globalThis.HTMLMediaElement.prototype, "play", {
      configurable: true,
      writable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
    Object.defineProperty(globalThis.HTMLAudioElement.prototype, "pause", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    Object.defineProperty(globalThis.HTMLAudioElement.prototype, "play", {
      configurable: true,
      writable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => "answer-sdp",
    });

    try {
      view = renderChat({
        sessionId: "sess-live-tool-config",
      });

      fireEvent.click(
        screen.getAllByRole("button", {
          name: /live streaming mode/i,
        })[0],
      );

      await waitFor(() => {
        expect(getSpy).toHaveBeenCalledWith("/api/tools/specs", {
          params: { workflow: "live" },
        });
        expect(activeDataChannel).toBeTruthy();
      });

      await act(async () => {
        activeDataChannel.emit(
          "message",
          JSON.stringify({
            type: "conversation.item.created",
            item: {
              type: "message",
              role: "user",
              id: "user-item-1",
              content: [
                {
                  type: "input_text",
                  text: "Use a tool if you need one.",
                },
              ],
            },
          }),
        );
      });

      await waitFor(() => {
        expect(screen.getByText("Use a tool if you need one.")).toBeInTheDocument();
      });
      expect(sentRealtimeEvents.some((event) => event.type === "response.create")).toBe(
        false,
      );

      resolveToolSpecs({
        data: {
          tools: [
            {
              name: "tool_info",
              description: "Inspect one tool",
              parameters: { type: "object", properties: {} },
              policy: { live_auto: true },
            },
          ],
        },
      });

      await waitFor(() => {
        const eventTypes = sentRealtimeEvents.map((event) => event.type);
        expect(eventTypes).toEqual(
          expect.arrayContaining(["session.update", "response.create"]),
        );
      });

      const sessionUpdateIndex = sentRealtimeEvents.findIndex(
        (event) => event.type === "session.update",
      );
      const responseCreateIndex = sentRealtimeEvents.findIndex(
        (event) => event.type === "response.create",
      );
      expect(sessionUpdateIndex).toBeGreaterThanOrEqual(0);
      expect(responseCreateIndex).toBeGreaterThan(sessionUpdateIndex);
    } finally {
      view?.unmount?.();
      getSpy.mockRestore();
      postSpy.mockRestore();
      global.fetch = originalFetch;
      Object.defineProperty(globalThis, "RTCPeerConnection", {
        configurable: true,
        writable: true,
        value: originalPeerConnection,
      });
      Object.defineProperty(globalThis, "webkitRTCPeerConnection", {
        configurable: true,
        writable: true,
        value: originalWebkitPeerConnection,
      });
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: originalMediaDevices,
      });
      Object.defineProperty(globalThis.HTMLMediaElement.prototype, "pause", {
        configurable: true,
        writable: true,
        value: originalMediaPause,
      });
      Object.defineProperty(globalThis.HTMLMediaElement.prototype, "play", {
        configurable: true,
        writable: true,
        value: originalMediaPlay,
      });
      Object.defineProperty(globalThis.HTMLAudioElement.prototype, "pause", {
        configurable: true,
        writable: true,
        value: originalAudioPause,
      });
      Object.defineProperty(globalThis.HTMLAudioElement.prototype, "play", {
        configurable: true,
        writable: true,
        value: originalAudioPlay,
      });
    }
  });
});
