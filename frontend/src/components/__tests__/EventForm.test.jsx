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
    fireEvent.click(screen.getByRole("button", { name: /update event/i }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toMatchObject({
      id: "nightly-reminder",
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
