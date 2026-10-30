import { afterEach, describe, expect, it, vi } from "vitest";
import { waitForChatResponse } from "../chatResponseRecovery";

describe("chat response delivery recovery", () => {
  afterEach(() => vi.useRealTimers());

  it("recovers only the submitted message after authoritative persistence, without resending", async () => {
    vi.useFakeTimers();
    const readConversation = vi.fn()
      .mockResolvedValueOnce({ messages: [{ id: "m", text: "intermediate", metadata: { status: "complete" } }] })
      .mockResolvedValueOnce({ messages: [{ id: "other", text: "unrelated", metadata: { status: "complete", timing: { completed_at: "now" } } }] })
      .mockResolvedValueOnce({ messages: [{ id: "m", text: "answer", metadata: { status: "complete", timing: { completed_at: "now" } } }] });
    const receive = vi.fn();
    const pending = waitForChatResponse(new Promise(() => {}), {
      readConversation, messageId: "m", pollMs: 1000, maxWaitMs: 10000,
    }).then(receive);
    await vi.advanceTimersByTimeAsync(2000);
    expect(receive).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    await pending;
    expect(receive).toHaveBeenCalledWith(expect.objectContaining({ message: "answer" }));
    expect(readConversation).toHaveBeenCalledTimes(3);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps waiting through slow generation and distinguishes unknown outcome from cancellation", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const readConversation = vi.fn().mockResolvedValue({ messages: [{ id: "m", metadata: { status: "generating" } }] });
    const pending = waitForChatResponse(new Promise(() => {}), {
      readConversation, messageId: "m", signal: controller.signal, pollMs: 1000, maxWaitMs: 10000,
    }).catch((error) => error);
    await vi.advanceTimersByTimeAsync(10000);
    expect((await pending).code).toBe("REQUEST_OUTCOME_UNKNOWN");
    expect(controller.signal.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("returns the original response promptly and removes recovery timers", async () => {
    vi.useFakeTimers();
    const readConversation = vi.fn();
    const result = await waitForChatResponse(Promise.resolve({ message: "done" }), {
      readConversation, messageId: "m",
    });
    expect(result.message).toBe("done");
    expect(readConversation).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("recovers actionable recorded proposals even though generation is awaiting review", async () => {
    vi.useFakeTimers();
    const pending = waitForChatResponse(new Promise(() => {}), {
      messageId: "m", pollMs: 1000,
      readConversation: async () => ({ messages: [{ id: "m", text: "Requested tools", metadata: { status: "pending", tool_response_pending: true }, tools: [{ id: "tool-1", name: "recall", status: "proposed" }] }] }),
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect((await pending).tools_used[0].id).toBe("tool-1");
    expect(vi.getTimerCount()).toBe(0);
  });
});
