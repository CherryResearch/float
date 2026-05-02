import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import Notifications from "../Notifications";
import { TOOL_REVIEW_ACTION_EVENT } from "../../utils/toolReviewActions";

const toolNotification = {
  title: "Tool review needed",
  body: "search_web is waiting for your review.",
  category: "tool_resolution",
  ts: 1710000000,
  data: {
    action_url: "/",
    tool_ids: ["proposal-1"],
    tool_names: ["search_web"],
    session_id: "sess-1",
    message_id: "msg-1",
    chain_id: "msg-1",
  },
};

describe("Notifications", () => {
  const originalFetch = global.fetch;
  const originalEventSource = global.EventSource;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ notifications: [toolNotification] }),
    });
    delete global.EventSource;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalFetch) {
      global.fetch = originalFetch;
    } else {
      delete global.fetch;
    }
    if (originalEventSource) {
      global.EventSource = originalEventSource;
    } else {
      delete global.EventSource;
    }
  });

  it("adds accept, deny, and edit actions to tool review notifications", async () => {
    const onOpenToolReview = vi.fn();
    const actions = [];
    const handler = (event) => actions.push({ ...event.detail });
    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handler);

    const { unmount } = render(
      <Notifications onOpenToolReview={onOpenToolReview} />,
    );

    const acceptButton = await screen.findByRole("button", { name: /^accept$/i });
    expect(screen.getByRole("button", { name: /^deny$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^edit$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^open$/i })).toBeInTheDocument();

    fireEvent.click(acceptButton);

    expect(onOpenToolReview).toHaveBeenCalledWith(
      expect.objectContaining({ toolId: "proposal-1", chainId: "msg-1" }),
    );
    expect(actions[0]).toEqual(
      expect.objectContaining({
        action: "accept",
        toolId: "proposal-1",
        chainId: "msg-1",
        handled: false,
      }),
    );

    window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handler);
    unmount();
  });

  it("tabs through batched tool review items and can accept plus continue the whole batch", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          notifications: [
            {
              ...toolNotification,
              data: {
                ...toolNotification.data,
                tool_ids: ["proposal-1", "proposal-2"],
                tool_names: ["search_web", "list_dir"],
              },
            },
          ],
        }),
    });
    const actions = [];
    const handler = (event) => actions.push({ ...event.detail });
    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handler);

    const { unmount } = render(<Notifications onOpenToolReview={() => {}} />);

    const first = await screen.findByRole("button", { name: /^1\. search_web$/i });
    const second = screen.getByRole("button", { name: /^2\. list_dir$/i });
    await waitFor(() => expect(first).toHaveClass("selected"));

    fireEvent.keyDown(window, { key: "Tab" });
    await waitFor(() => expect(second).toHaveClass("selected"));

    fireEvent.keyDown(window, { key: "y" });
    await waitFor(() => {
      expect(actions[0]).toEqual(
        expect.objectContaining({
          action: "accept",
          scope: "selected",
          toolId: "proposal-2",
          selectedToolId: "proposal-2",
          toolIds: ["proposal-2"],
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /accept & continue batch/i }));
    await waitFor(() => {
      expect(
        actions.some(
          (action) =>
            action.action === "accept" &&
            action.toolId === "proposal-1" &&
            action.scope === "selected",
        ),
      ).toBe(true);
      expect(
        actions.some(
          (action) =>
            action.action === "accept" &&
            action.toolId === "proposal-2" &&
            action.scope === "selected",
        ),
      ).toBe(true);
      const batchAction = actions.find((action) => action.action === "continue");
      expect(batchAction).toEqual(
        expect.objectContaining({
          action: "continue",
          scope: "batch",
          toolIds: ["proposal-1", "proposal-2"],
        }),
      );
    });

    window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handler);
    unmount();
  });

  it("maps Y, N, Alt+N, and Ctrl+Y while the toast is visible", async () => {
    const actions = [];
    const handler = (event) => actions.push({ ...event.detail });
    window.addEventListener(TOOL_REVIEW_ACTION_EVENT, handler);

    const { unmount } = render(<Notifications onOpenToolReview={() => {}} />);
    await screen.findByRole("button", { name: /^accept$/i });

    fireEvent.keyDown(window, { key: "y" });
    fireEvent.keyDown(window, { key: "n" });
    fireEvent.keyDown(window, { key: "n", altKey: true });

    await waitFor(() => {
      expect(actions.map((action) => action.action).slice(0, 3)).toEqual([
        "accept",
        "deny",
        "edit",
      ]);
    });
    expect(screen.getByRole("button", { name: /^edit$/i })).toHaveClass(
      "shortcut-active",
    );

    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    await waitFor(() => {
      expect(actions.some((action) => action.action === "continue")).toBe(true);
    });

    window.removeEventListener(TOOL_REVIEW_ACTION_EVENT, handler);
    unmount();
  });

  it("updates operation progress notifications in place and shows stage/timing details", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ notifications: [] }),
    });

    const sources = [];

    class FakeEventSource {
      constructor() {
        this.listeners = {};
        this.onmessage = null;
        sources.push(this);
      }

      addEventListener(type, handler) {
        this.listeners[type] = handler;
      }

      emit(type, payload) {
        const event = { data: JSON.stringify(payload) };
        if (this.listeners[type]) this.listeners[type](event);
        if (type === "notification" && typeof this.onmessage === "function") {
          this.onmessage(event);
        }
      }

      close() {}
    }

    global.EventSource = FakeEventSource;

    const { unmount } = render(<Notifications onOpenToolReview={() => {}} />);

    await waitFor(() => expect(sources).toHaveLength(1));

    act(() => {
      sources[0].emit("notification", {
        title: "Indexing image attachment",
        body: "progress.png",
        category: "operation_progress",
        data: {
          operation_id: "attachment-index:hash-1",
          status: "running",
          phase_label: "Generating image caption",
          phase_index: 2,
          phase_count: 4,
          started_at: "2026-04-19T00:00:00Z",
          detail: "Running the caption step for the uploaded image.",
        },
      });
    });

    expect(await screen.findByText(/Generating image caption/i)).toBeInTheDocument();
    expect(screen.getByText(/Step 2 of 4/i)).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: /operation progress/i }),
    ).toBeInTheDocument();

    act(() => {
      sources[0].emit("notification", {
        title: "Indexing image attachment",
        body: "progress.png",
        category: "operation_progress",
        data: {
          operation_id: "attachment-index:hash-1",
          status: "complete",
          phase_label: "Attachment indexing finished",
          phase_index: 4,
          phase_count: 4,
          elapsed_ms: 1800,
          detail: "Caption and index data are ready.",
        },
      });
    });

    expect(await screen.findByText(/Attachment indexing finished/i)).toBeInTheDocument();
    expect(screen.getByText(/1\.8 s/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Indexing image attachment/i)).toHaveLength(1);

    unmount();
  });
});
