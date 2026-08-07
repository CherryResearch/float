import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";
import axios from "axios";
import CalendarTab from "../CalendarTab";
import { GlobalContext } from "../../main";

describe("CalendarTab recurring jobs", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders expanded dense occurrences as one readable series", async () => {
    const start = Date.parse("2026-07-25T20:00:00-07:00") / 1000;
    const baseEvent = {
      id: "continuous-review",
      title: "Continuous review",
      start_time: start,
      end_time: start + 60,
      timezone: "America/Vancouver",
      rrule: "FREQ=MINUTELY;INTERVAL=2;COUNT=30",
      status: "scheduled",
      actions: [{ kind: "prompt", prompt: "Review the queue" }],
    };
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/calendar/events") {
        return Promise.resolve({ data: { events: [baseEvent] } });
      }
      if (url === "/api/calendar/occurrences") {
        return Promise.resolve({
          data: {
            occurrences: [0, 120, 240].map((offset) => ({
              ...baseEvent,
              source_event_id: baseEvent.id,
              occurrence_id: `${baseEvent.id}:${start + offset}`,
              start_time: start + offset,
              end_time: start + offset + 60,
            })),
            truncated: [
              {
                event_id: baseEvent.id,
                title: baseEvent.title,
                limit: 2048,
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    render(
      <MemoryRouter>
        <GlobalContext.Provider
          value={{
            state: {
              calendarEvents: [],
              selectedCalendarDate: new Date("2026-07-25T12:00:00-07:00"),
              userTimezone: "America/Vancouver",
            },
            setState: vi.fn(),
          }}
        >
          <CalendarTab />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/3 runs/i)).toBeInTheDocument();
    expect(screen.getByText(/every 2 minutes/i)).toBeInTheDocument();
    expect(screen.getByText(/showing the first 2048 occurrences/i)).toBeInTheDocument();
    expect(screen.getByText(/current-session work stays in agent console/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open activity/i })).toHaveAttribute(
      "href",
      "/work-history",
    );
    expect(screen.getByRole("button", { name: /end future series/i })).toHaveAttribute(
      "title",
      "End future occurrences without stopping a run already in progress",
    );
    expect(screen.queryByRole("button", { name: /^stop series$/i })).not.toBeInTheDocument();
  });

  it("separates a running occurrence stop from ending its future series", async () => {
    const start = Date.parse("2026-07-25T20:00:00-07:00") / 1000;
    const runningEvent = {
      id: "running-review",
      title: "Running review",
      start_time: start,
      timezone: "America/Vancouver",
      rrule: "FREQ=DAILY;COUNT=30",
      status: "followup-running",
      actions: [{ kind: "prompt", prompt: "Review" }],
    };
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/calendar/events") {
        return Promise.resolve({ data: { events: [runningEvent] } });
      }
      if (url === "/api/calendar/occurrences") {
        return Promise.resolve({
          data: {
            occurrences: [
              {
                ...runningEvent,
                source_event_id: runningEvent.id,
                occurrence_id: `${runningEvent.id}:${start}`,
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    render(
      <MemoryRouter>
        <GlobalContext.Provider
          value={{
            state: {
              calendarEvents: [],
              selectedCalendarDate: new Date("2026-07-25T12:00:00-07:00"),
              userTimezone: "America/Vancouver",
            },
            setState: vi.fn(),
          }}
        >
          <CalendarTab />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: /end future series/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^stop series$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /request current run stop/i })).toHaveAttribute(
      "href",
      "/work-history",
    );
  });

  it("shows active-run deletion guidance returned by the backend", async () => {
    const start = Date.parse("2026-07-25T20:00:00-07:00") / 1000;
    const runningEvent = {
      id: "running-delete-guard",
      title: "Running delete guard",
      start_time: start,
      timezone: "America/Vancouver",
      status: "running",
      actions: [{ kind: "prompt", prompt: "Review" }],
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/calendar/events") {
        return Promise.resolve({ data: { events: [runningEvent] } });
      }
      if (url === "/api/calendar/occurrences") {
        return Promise.resolve({
          data: {
            occurrences: [
              {
                ...runningEvent,
                source_event_id: runningEvent.id,
                occurrence_id: `${runningEvent.id}:${start}`,
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.spyOn(axios, "delete").mockRejectedValue({
      response: {
        data: {
          detail:
            "This event has an active run. Use End future series for recurring work and Request current run stop in Activity, then delete the event after the run is terminal.",
        },
      },
    });

    render(
      <MemoryRouter>
        <GlobalContext.Provider
          value={{
            state: {
              calendarEvents: [],
              selectedCalendarDate: new Date("2026-07-25T12:00:00-07:00"),
              userTimezone: "America/Vancouver",
            },
            setState: vi.fn(),
          }}
        >
          <CalendarTab />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(screen.getByText(/request current run stop in activity/i)).toBeInTheDocument(),
    );
    expect(axios.delete).toHaveBeenCalledWith(
      "/api/calendar/events/running-delete-guard",
    );
  });

  it("presents paused series with an explicit resume control", async () => {
    const start = Date.parse("2026-07-25T20:00:00-07:00") / 1000;
    const pausedEvent = {
      id: "paused-review",
      title: "Paused review",
      start_time: start,
      timezone: "America/Vancouver",
      rrule: "FREQ=DAILY;COUNT=30",
      status: "paused",
      actions: [{ kind: "prompt", prompt: "Review" }],
    };
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/calendar/events") {
        return Promise.resolve({ data: { events: [pausedEvent] } });
      }
      if (url === "/api/calendar/occurrences") {
        return Promise.resolve({
          data: {
            occurrences: [
              {
                ...pausedEvent,
                source_event_id: pausedEvent.id,
                occurrence_id: `${pausedEvent.id}:${start}`,
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    render(
      <MemoryRouter>
        <GlobalContext.Provider
          value={{
            state: {
              calendarEvents: [],
              selectedCalendarDate: new Date("2026-07-25T12:00:00-07:00"),
              userTimezone: "America/Vancouver",
            },
            setState: vi.fn(),
          }}
        >
          <CalendarTab />
        </GlobalContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/paused series \(1\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resume series/i })).toBeInTheDocument();
  });
});
