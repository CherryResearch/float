import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import axios from "axios";
import { afterEach, beforeEach, vi } from "vitest";
import EventForm from "../EventForm";

describe("EventForm", () => {
  beforeEach(() => {
    vi.spyOn(axios, "get").mockResolvedValue({ data: { tools: [] } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([true, false])(
    "creates distinct events with repeated titles and incremental typing (UUID available: %s)",
    async (uuidAvailable) => {
      vi.spyOn(Date, "now").mockReturnValue(1788307200000);
      if (!uuidAvailable) vi.stubGlobal("crypto", {});
      const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { status: "saved" } });
      const createdIds = [];

      for (let eventIndex = 0; eventIndex < 2; eventIndex += 1) {
        const view = render(
          <EventForm
            event={null}
            selectedDate={new Date("2026-09-12T09:00:00")}
            isOpen
            onSaved={() => {}}
            onCancel={() => {}}
          />,
        );
        fireEvent.click(screen.getByRole("button", { name: /advanced settings/i }));
        const idInput = screen.getByPlaceholderText(/auto-generated/i);
        const originalId = idInput.value;
        const titleInput = screen.getByPlaceholderText("Event name");
        const title = "Morning review";
        for (let length = 1; length <= title.length; length += 1) {
          fireEvent.change(titleInput, { target: { value: title.slice(0, length) } });
        }
        expect(idInput).toHaveValue(originalId);
        fireEvent.click(screen.getByRole("button", { name: /create event/i }));
        await waitFor(() => expect(post).toHaveBeenCalledTimes(eventIndex + 1));
        const [url, payload] = post.mock.calls[eventIndex];
        expect(payload).toMatchObject({ id: originalId, title });
        expect(url).toBe(`/api/calendar/events/${encodeURIComponent(originalId)}`);
        createdIds.push(payload.id);
        view.unmount();
      }

      expect(createdIds[0]).not.toBe(createdIds[1]);
    },
  );

  it("preserves an explicit new-event ID while the title changes", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { status: "saved" } });
    render(
      <EventForm
        event={null}
        selectedDate={new Date("2026-09-12T09:00:00")}
        isOpen
        onSaved={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /advanced settings/i }));
    fireEvent.change(screen.getByPlaceholderText(/auto-generated/i), {
      target: { value: "custom-review-id" },
    });
    fireEvent.change(screen.getByPlaceholderText("Event name"), {
      target: { value: "Renamed review" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create event/i }));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        "/api/calendar/events/custom-review-id",
        expect.objectContaining({ id: "custom-review-id", title: "Renamed review" }),
      );
    });
  });

  it("does not reset fields while typing", () => {
    render(
      <EventForm
        event={null}
        selectedDate={new Date("2025-12-27T22:53:00")}
        isOpen
        onSaved={() => {}}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit \(0\)/i }));
    const actionsDraft = screen.getByPlaceholderText(/type a tool name/i);
    fireEvent.change(actionsDraft, { target: { value: "Write follow-up plan" } });
    expect(actionsDraft).toHaveValue("Write follow-up plan");

    const startInput = screen.getByLabelText(/Start/i);
    fireEvent.change(startInput, { target: { value: "2025-12-27T22:55" } });
    expect(startInput).toHaveValue("2025-12-27T22:55");

    fireEvent.click(
      screen.getByRole("button", { name: /Advanced settings/i }),
    );
    expect(screen.getByText(/Event ID/i)).toBeInTheDocument();

    const idInput = screen.getByPlaceholderText(/auto-generated/i);
    fireEvent.change(idInput, { target: { value: "custom-event-id" } });
    expect(idInput).toHaveValue("custom-event-id");
    expect(screen.getByText(/Event ID/i)).toBeInTheDocument();
    expect(actionsDraft).toHaveValue("Write follow-up plan");
  });

  it("keeps one explicit close action as long editor sections expand", () => {
    const onCancel = vi.fn();
    render(
      <EventForm
        event={null}
        selectedDate={new Date("2026-07-31T20:00:00-07:00")}
        isOpen
        onSaved={() => {}}
        onCancel={onCancel}
      />,
    );

    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: /close event editor/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /edit \(0\)/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /advanced settings/i }));

    expect(within(dialog).getByRole("button", { name: /close event editor/i })).toBe(close);
    expect(within(dialog).getAllByRole("button", { name: /close event editor/i })).toHaveLength(1);
    expect(close).toHaveTextContent("Close");

    fireEvent.click(close);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("can clear recurrence and stale job policy from a reminder-only event", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { status: "saved" } });
    render(
      <EventForm
        event={{
          id: "nightly-reminder",
          title: "Nightly reminder",
          startDate: new Date("2026-07-26T03:00:00Z"),
          endDate: new Date("2026-07-26T03:30:00Z"),
          timezone: "America/Vancouver",
          rrule: "FREQ=DAILY;INTERVAL=1;COUNT=365",
          actions: [],
          background_job: { patience: { stop_condition: "until_useful" } },
        }}
        selectedDate={new Date("2026-07-25T20:00:00-07:00")}
        isOpen
        onSaved={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByRole("group", { name: /schedule/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/repeat/i), {
      target: { value: "once" },
    });
    fireEvent.change(screen.getByPlaceholderText("Event name"), {
      target: { value: "Renamed reminder" },
    });
    fireEvent.click(screen.getByRole("button", { name: /update event/i }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toMatchObject({
      id: "nightly-reminder",
      title: "Renamed reminder",
      rrule: null,
      background_job: null,
      start_time: Date.parse("2026-07-26T03:00:00Z") / 1000,
    });
  });

  it("explains that scheduled permission scopes are enforced", () => {
    render(
      <EventForm
        event={{
          id: "permission-review",
          title: "Permission review",
          startDate: new Date("2026-08-01T03:00:00Z"),
          timezone: "America/Vancouver",
          actions: [
            {
              id: "remember-action",
              kind: "tool",
              name: "remember",
              args: { key: "review", value: "ready" },
              status: "scheduled",
            },
          ],
          background_job: { execution: { permissions: ["memory.write"] } },
        }}
        selectedDate={new Date("2026-07-31T20:00:00-07:00")}
        isOpen
        onSaved={() => {}}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /recorded execution policy/i }),
    );

    expect(screen.getByText("Allowed permission scopes")).toBeInTheDocument();
    expect(screen.getByDisplayValue("memory.write")).toBeInTheDocument();
    expect(
      screen.getByText(/Allowed scopes apply to the whole scheduled job/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Float pauses before a tool call when one of its required scopes is missing/i),
    ).toBeInTheDocument();
  });
});
