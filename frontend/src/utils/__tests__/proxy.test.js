import { afterEach, describe, expect, it, vi } from "vitest";

import {
  debugLog,
  getConversationTrimMeta,
  isConversationDetailRequest,
  memoryStore,
  trimConversationMessagesForDom,
  trimConversationResponseForDom,
} from "../proxy";

describe("proxy debug logging", () => {
  afterEach(() => {
    localStorage.clear();
    delete window.__FLOAT_DEBUG_LOGS__;
    vi.restoreAllMocks();
  });

  it("keeps routine proxy logs silent by default", () => {
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});

    debugLog("chat payload", { message: "hello" });
    memoryStore.quiet = { content: "hello", importance: 1 };

    expect(debugSpy).not.toHaveBeenCalled();
  });

  it("emits debug logs only when explicitly enabled", () => {
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
    localStorage.setItem("floatDebugLogs", "true");

    debugLog("chat payload", { message: "hello" });

    expect(debugSpy).toHaveBeenCalledWith("chat payload", { message: "hello" });
  });
});

describe("conversation payload trimming", () => {
  afterEach(() => {
    localStorage.clear();
    delete window.__FLOAT_CONVERSATION_MESSAGE_LIMIT__;
    delete window.__FLOAT_CONVERSATION_TOOL_LIMIT__;
  });

  it("keeps only the recent message window and marks the payload as partial", () => {
    window.__FLOAT_CONVERSATION_MESSAGE_LIMIT__ = 3;
    const messages = Array.from({ length: 6 }, (_, index) => ({
      id: `m-${index}`,
      role: index % 2 ? "ai" : "user",
      text: `message ${index}`,
    }));

    const result = trimConversationMessagesForDom(messages, { source: "test" });

    expect(result.messages.map((message) => message.id)).toEqual(["m-3", "m-4", "m-5"]);
    expect(result.meta).toMatchObject({
      truncated: true,
      source: "test",
      total_messages: 6,
      omitted_messages: 3,
      start_index: 3,
      message_limit: 3,
    });
    expect(getConversationTrimMeta(result.messages)).toEqual(result.meta);
  });

  it("caps oversized tool arrays inside the retained messages", () => {
    window.__FLOAT_CONVERSATION_TOOL_LIMIT__ = 2;
    const messages = [
      {
        id: "assistant",
        role: "ai",
        text: "done",
        tools: [
          { name: "first" },
          { name: "second" },
          { name: "third" },
        ],
        metadata: {
          inline_tool_payloads: ["one", "two", "three"],
        },
      },
    ];

    const result = trimConversationMessagesForDom(messages, { source: "test" });

    expect(result.messages[0].tools.map((tool) => tool.name)).toEqual(["second", "third"]);
    expect(result.messages[0].metadata.inline_tool_payloads).toEqual(["two", "three"]);
    expect(result.messages[0].metadata.client_trim).toMatchObject({
      omitted_tools: 2,
      tool_limit: 2,
    });
    expect(result.meta).toMatchObject({
      truncated: true,
      omitted_tools: 2,
      tool_limit: 2,
    });
  });

  it("trims axios conversation detail responses before callers receive them", () => {
    window.__FLOAT_CONVERSATION_MESSAGE_LIMIT__ = 2;
    const response = {
      config: {
        method: "get",
        url: "/api/conversations/folder%2Fchat",
      },
      data: {
        messages: [
          { id: "old", text: "old" },
          { id: "middle", text: "middle" },
          { id: "new", text: "new" },
        ],
      },
    };

    const trimmed = trimConversationResponseForDom(response);

    expect(trimmed.data.messages.map((message) => message.id)).toEqual(["middle", "new"]);
    expect(getConversationTrimMeta(trimmed.data)).toMatchObject({
      truncated: true,
      total_messages: 3,
      omitted_messages: 1,
      start_index: 1,
    });
  });

  it("ignores conversation collection and export requests", () => {
    expect(isConversationDetailRequest({ method: "get", url: "/api/conversations" })).toBe(false);
    expect(
      isConversationDetailRequest({
        method: "get",
        url: "/api/conversations/export-all",
      }),
    ).toBe(false);
    expect(
      isConversationDetailRequest({
        method: "get",
        url: "/api/conversations/demo/export",
      }),
    ).toBe(false);
  });
});
