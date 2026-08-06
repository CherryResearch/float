import React, {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import "../styles/Calendar.css";
import WeekView from "./WeekView";
import EventForm from "./EventForm";
import { GlobalContext } from "../main";
import axios from "axios";
import { Link } from "react-router-dom";
import FilterBar from "./FilterBar";
import { Line, Rect } from "./Skeleton";
import {
  buildMonthGridDates,
  formatStatusLabel,
  isClearedCalendarStatus,
} from "../utils/calendarPanel";
import { recurrenceSummary } from "../utils/backgroundJobPolicy";
import {
  civilDateKey,
  dateKeyInTimeZone,
  formatTimeInTimeZone,
  zonedInputToDate,
} from "../utils/zonedDateTime";

const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const startOfMonth = (date) => new Date(date.getFullYear(), date.getMonth(), 1);

const formatTimeRange = (event, timeZone) => {
  if (!event?.startDate) return "All day";
  const start = event.startDate;
  const end = event.endDate;
  if (!end) {
    return formatTimeInTimeZone(start, timeZone);
  }
  return `${formatTimeInTimeZone(start, timeZone)} - ${formatTimeInTimeZone(
    end,
    timeZone,
  )}`;
};

const CalendarTab = ({ focusEventId = null }) => {
  const { state, setState } = useContext(GlobalContext);
  const now = useMemo(() => new Date(), []);
  const [currentMonth, setCurrentMonth] = useState(() => startOfMonth(now));
  const [viewMode, setViewMode] = useState("month");
  const [evtQ, setEvtQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [eventError, setEventError] = useState("");
  const [occurrenceError, setOccurrenceError] = useState("");
  const [activeEvent, setActiveEvent] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [busyDeleteEventId, setBusyDeleteEventId] = useState("");
  const [busyStatusEventId, setBusyStatusEventId] = useState("");
  const [ragBusy, setRagBusy] = useState(false);
  const [ragStatus, setRagStatus] = useState(null);
  const [showClearedEvents, setShowClearedEvents] = useState(false);
  const [calendarOccurrences, setCalendarOccurrences] = useState([]);
  const [occurrencesReady, setOccurrencesReady] = useState(false);
  const occurrenceRequestRef = useRef(0);

  const { calendarEvents = [] } = state;
  const displayTimezone = useMemo(
    () =>
      (typeof state.userTimezone === "string" && state.userTimezone.trim()) ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "UTC",
    [state.userTimezone],
  );

  const selectedDate = useMemo(() => {
    const raw = state.selectedCalendarDate;
    if (!raw) return new Date(now);
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return new Date(now);
    return parsed;
  }, [state.selectedCalendarDate, now]);

  const setSelectedDate = useCallback(
    (date) => {
      const next = new Date(date);
      if (Number.isNaN(next.getTime())) return;
      setState((prev) => ({ ...prev, selectedCalendarDate: next }));
      setCurrentMonth(startOfMonth(next));
    },
    [setState],
  );

  const normalizeEvent = useCallback((event) => {
    if (!event) return null;
    const toDate = (value, fallback) => {
      if (typeof value === "number") {
        return new Date(value * 1000);
      }
      if (value && typeof value === "object" && value.dateTime) {
        return new Date(value.dateTime);
      }
      if (typeof value === "string") {
        return new Date(value);
      }
      return fallback ?? null;
    };
    const startDate = toDate(event.start_time, toDate(event.start, null));
    const endDate = toDate(event.end_time, toDate(event.end, null));
    return {
      ...event,
      summary: event.title || event.summary || event.id,
      start:
        event.start ||
        (startDate ? { dateTime: startDate.toISOString() } : undefined),
      end:
        event.end || (endDate ? { dateTime: endDate.toISOString() } : undefined),
      startDate: startDate && !Number.isNaN(startDate.getTime()) ? startDate : null,
      endDate: endDate && !Number.isNaN(endDate.getTime()) ? endDate : null,
    };
  }, []);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setEventError("");
    try {
      const res = await axios.get("/api/calendar/events", {
        params: { detailed: true },
      });
      const raw = Array.isArray(res?.data?.events) ? res.data.events : [];
      const normalized = raw
        .map((evt) => normalizeEvent(evt))
        .filter((evt) => evt && evt.id);
      setState((prev) => ({ ...prev, calendarEvents: normalized }));
      return normalized;
    } catch (err) {
      console.error("Failed to fetch calendar events", err);
      setEventError("Unable to load events right now. Please try again.");
      return [];
    } finally {
      setLoading(false);
    }
  }, [normalizeEvent, setState]);

  const loadOccurrences = useCallback(async () => {
    const requestId = occurrenceRequestRef.current + 1;
    occurrenceRequestRef.current = requestId;
    const viewport = buildMonthGridDates(currentMonth);
    const first =
      zonedInputToDate(`${civilDateKey(viewport[0])}T00:00:00`, displayTimezone) ||
      new Date(viewport[0]);
    const last =
      zonedInputToDate(
        `${civilDateKey(viewport[viewport.length - 1])}T23:59:59`,
        displayTimezone,
      ) || new Date(viewport[viewport.length - 1]);
    setOccurrencesReady(false);
    try {
      const res = await axios.get("/api/calendar/occurrences", {
        params: {
          range_start: Math.floor(first.getTime() / 1000),
          range_end: Math.floor(last.getTime() / 1000),
          limit_per_event: 2048,
          include_inactive: true,
        },
      });
      if (occurrenceRequestRef.current !== requestId) return;
      const raw = Array.isArray(res?.data?.occurrences)
        ? res.data.occurrences
        : [];
      const recurrenceErrors = Array.isArray(res?.data?.errors) ? res.data.errors : [];
      const truncated = Array.isArray(res?.data?.truncated) ? res.data.truncated : [];
      const issueMessages = [];
      if (recurrenceErrors.length) {
        issueMessages.push(
          `Could not expand recurrence for ${recurrenceErrors
              .map((item) => item?.title || item?.event_id)
              .filter(Boolean)
              .join(", ")}. Edit its advanced RRULE.`,
        );
      }
      if (truncated.length) {
        issueMessages.push(
          `Showing the first ${truncated[0]?.limit || 2048} occurrences for ${truncated
            .map((item) => item?.title || item?.event_id)
            .filter(Boolean)
            .join(", ")}; narrow the Calendar range to inspect every run.`,
        );
      }
      setOccurrenceError(issueMessages.join(" "));
      setCalendarOccurrences(
        raw.map((event) => normalizeEvent(event)).filter((event) => event?.id),
      );
      setOccurrencesReady(true);
    } catch (err) {
      if (occurrenceRequestRef.current !== requestId) return;
      console.error("Failed to expand calendar occurrences", err);
      setCalendarOccurrences([]);
      setOccurrencesReady(false);
      setOccurrenceError("Recurring occurrences are unavailable right now.");
    }
  }, [currentMonth, displayTimezone, normalizeEvent]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    loadOccurrences();
    return () => {
      occurrenceRequestRef.current += 1;
    };
  }, [loadOccurrences]);

  useEffect(() => {
    if (!focusEventId) return;
    const idStr = String(focusEventId);
    if (activeEvent && String(activeEvent.id) === idStr) return;
    const match = calendarEvents.find((evt) => String(evt.id) === idStr);
    if (match) {
      setEvtQ(idStr);
      handleEditEvent(match);
    } else {
      setEvtQ(idStr);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusEventId, calendarEvents]);

  useEffect(() => {
    setCurrentMonth(startOfMonth(selectedDate));
  }, [selectedDate]);

  const filteredEvents = useMemo(() => {
    const needle = (evtQ || "").trim().toLowerCase();
    const source = occurrencesReady ? calendarOccurrences : calendarEvents;
    if (!needle) return source;
    return source.filter((evt) => {
      const haystack = [evt.summary, evt.title, evt.description, evt.status, evt.rrule]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [calendarEvents, calendarOccurrences, evtQ, occurrencesReady]);

  const eventDates = useMemo(() => {
    const dates = new Set();
    filteredEvents.forEach((evt) => {
      if (evt?.startDate && !isClearedCalendarStatus(evt?.status)) {
        dates.add(dateKeyInTimeZone(evt.startDate, displayTimezone));
      }
    });
    return dates;
  }, [displayTimezone, filteredEvents]);

  const scheduledEventsForWeek = useMemo(
    () => filteredEvents.filter((evt) => !isClearedCalendarStatus(evt?.status)),
    [filteredEvents],
  );

  const occurrencesForSelectedDay = useMemo(() => {
    const target = civilDateKey(selectedDate);
    return filteredEvents
      .filter(
        (evt) =>
          evt.startDate && dateKeyInTimeZone(evt.startDate, displayTimezone) === target,
      )
      .sort((a, b) => {
        const aTime = a.startDate ? a.startDate.getTime() : 0;
        const bTime = b.startDate ? b.startDate.getTime() : 0;
        return aTime - bTime;
      });
  }, [displayTimezone, filteredEvents, selectedDate]);

  const eventsForSelectedDay = useMemo(() => {
    const groups = new Map();
    occurrencesForSelectedDay.forEach((event) => {
      const sourceId = String(event.source_event_id || event.id);
      const existing = groups.get(sourceId);
      if (!existing) {
        groups.set(sourceId, {
          ...event,
          source_event_id: sourceId,
          occurrenceCount: 1,
          lastOccurrenceDate: event.startDate,
        });
        return;
      }
      existing.occurrenceCount += 1;
      if (
        event.startDate &&
        (!existing.lastOccurrenceDate || event.startDate > existing.lastOccurrenceDate)
      ) {
        existing.lastOccurrenceDate = event.startDate;
      }
    });
    return [...groups.values()];
  }, [occurrencesForSelectedDay]);

  const activeEventsForSelectedDay = useMemo(
    () =>
      eventsForSelectedDay.filter(
        (evt) =>
          !isClearedCalendarStatus(evt?.status) &&
          String(evt?.status || "").toLowerCase() !== "paused",
      ),
    [eventsForSelectedDay],
  );

  const clearedEventsForSelectedDay = useMemo(
    () =>
      eventsForSelectedDay.filter((evt) => isClearedCalendarStatus(evt?.status)),
    [eventsForSelectedDay],
  );

  useEffect(() => {
    if (!clearedEventsForSelectedDay.length) {
      setShowClearedEvents(false);
    }
  }, [clearedEventsForSelectedDay.length]);

  const prevMonth = () => {
    setCurrentMonth((prev) =>
      startOfMonth(new Date(prev.getFullYear(), prev.getMonth() - 1, 1)),
    );
  };

  const nextMonth = () => {
    setCurrentMonth((prev) =>
      startOfMonth(new Date(prev.getFullYear(), prev.getMonth() + 1, 1)),
    );
  };

  const goToday = () => {
    const today = new Date();
    setSelectedDate(today);
  };

  const dates = useMemo(() => {
    return buildMonthGridDates(currentMonth);
  }, [currentMonth]);

  const isSameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();

  const weekLabel = () => {
    const start = new Date(selectedDate);
    start.setDate(selectedDate.getDate() - selectedDate.getDay());
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    return `${start.toLocaleDateString()} - ${end.toLocaleDateString()}`;
  };

  const handleRefresh = () => {
    loadEvents();
    loadOccurrences();
  };

  const rehydrateRag = async () => {
    setRagBusy(true);
    setRagStatus(null);
    try {
      const res = await axios.post("/api/calendar/rag/rehydrate", {
        dry_run: false,
      });
      setRagStatus(res.data || null);
    } catch (err) {
      console.error("calendar RAG rehydrate failed", err);
      setRagStatus({ error: "rehydrate_failed" });
    } finally {
      setRagBusy(false);
    }
  };

  const handleEditEvent = (evt) => {
    const sourceId = String(evt?.source_event_id || evt?.id || "");
    const source = calendarEvents.find((event) => String(event.id) === sourceId);
    setActiveEvent(source || evt || null);
    if (evt?.startDate) {
      const [year, month, day] = dateKeyInTimeZone(
        evt.startDate,
        displayTimezone,
      )
        .split("-")
        .map(Number);
      setSelectedDate(new Date(year, month - 1, day, 12));
    }
    setFormOpen(true);
  };

  const buildEventPayload = useCallback(
    (evt, overrides = {}) => ({
      id: evt?.id,
      title: evt?.title || evt?.summary || evt?.id || "Untitled event",
      description: evt?.description || undefined,
      location: evt?.location || undefined,
      start_time:
        evt?.start_time ??
        (evt?.startDate ? Math.floor(evt.startDate.getTime() / 1000) : undefined),
      end_time:
        evt?.end_time ??
        (evt?.endDate ? Math.floor(evt.endDate.getTime() / 1000) : undefined),
      grounded_at: evt?.grounded_at,
      rrule: evt?.rrule || undefined,
      timezone:
        evt?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      notes: Array.isArray(evt?.notes) ? evt.notes : [],
      actions: Array.isArray(evt?.actions) ? evt.actions : [],
      status: evt?.status || "pending",
      ...overrides,
    }),
    [],
  );

  const handleSetEventStatus = useCallback(
    async (evt, status) => {
      const eventId = String(evt?.source_event_id || evt?.id || "");
      if (!eventId) return;
      const source =
        calendarEvents.find((event) => String(event.id) === eventId) || evt;
      setBusyStatusEventId(eventId);
      setEventError("");
      try {
        await axios.post(
          `/api/calendar/events/${encodeURIComponent(eventId)}`,
          buildEventPayload(source, { id: eventId, status }),
        );
        await loadEvents();
        await loadOccurrences();
        if (activeEvent && activeEvent.id === eventId) {
          setActiveEvent((current) => (current ? { ...current, status } : current));
        }
      } catch (err) {
        console.error("Failed to update calendar event status", err);
        setEventError("Failed to update event status. Please try again.");
      } finally {
        setBusyStatusEventId("");
      }
    },
    [
      activeEvent,
      buildEventPayload,
      calendarEvents,
      loadEvents,
      loadOccurrences,
    ],
  );

  const pausedEventsForSelectedDay = useMemo(
    () =>
      eventsForSelectedDay.filter(
        (evt) => String(evt?.status || "").toLowerCase() === "paused",
      ),
    [eventsForSelectedDay],
  );

  const handleDeleteEvent = async (eventId) => {
    if (!eventId) return;
    if (!window.confirm(`Delete event "${eventId}"?`)) return;
    setBusyDeleteEventId(eventId);
    setEventError("");
    try {
      await axios.delete(`/api/calendar/events/${encodeURIComponent(eventId)}`);
      await loadEvents();
      await loadOccurrences();
      if (activeEvent && activeEvent.id === eventId) {
        setActiveEvent(null);
        setFormOpen(false);
      }
    } catch (err) {
      console.error("Failed to delete calendar event", err);
      const detail = err?.response?.data?.detail;
      setEventError(
        typeof detail === "string" && detail.trim()
          ? detail
          : "Failed to delete event. Please try again.",
      );
    } finally {
      setBusyDeleteEventId("");
    }
  };

  const handleEventSaved = async () => {
    await loadEvents();
    await loadOccurrences();
    setActiveEvent(null);
    setFormOpen(false);
  };

  const handleGoogleImport = useCallback(async () => {
    const data = window.prompt("Paste Google Calendar API JSON");
    if (!data) return;
    try {
      await fetch("/api/calendar/import/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: data,
      });
      await loadEvents();
      await loadOccurrences();
    } catch (err) {
      console.error(err);
      setEventError("Failed to import Google Calendar data.");
    }
  }, [loadEvents, loadOccurrences]);

  const handleNewEvent = () => {
    setActiveEvent(null);
    setFormOpen(true);
  };

  const handleICSUpload = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const formData = new FormData();
      formData.append("file", file);
      try {
        await fetch("/api/calendar/import/ics", {
          method: "POST",
          body: formData,
        });
        await loadEvents();
        await loadOccurrences();
      } catch (err) {
        console.error(err);
        setEventError("Failed to import .ics file.");
      } finally {
        e.target.value = "";
      }
    },
    [loadEvents, loadOccurrences],
  );

  const renderEventRow = (evt) => {
    const eventId = String(evt?.source_event_id || evt?.id || "");
    const cleared = isClearedCalendarStatus(evt?.status);
    const normalizedStatus = String(evt?.status || "").toLowerCase();
    const paused = normalizedStatus === "paused";
    const hasCurrentRun = ["running", "followup-running", "followup_running"].includes(
      normalizedStatus,
    );
    const isBusy = busyStatusEventId === eventId || busyDeleteEventId === eventId;
    const recurring = Boolean(evt?.rrule);
    const denseLabel =
      evt.occurrenceCount > 1 && evt.lastOccurrenceDate
        ? `${evt.occurrenceCount} runs · ${formatTimeInTimeZone(
            evt.startDate,
            displayTimezone,
          )}–${formatTimeInTimeZone(evt.lastOccurrenceDate, displayTimezone)}`
        : null;
    return (
      <li
        key={evt.occurrence_id || eventId}
        className={`calendar-event-row${cleared ? " is-cleared" : ""}`}
      >
        <div className="calendar-event-meta">
          <div className="calendar-event-title-row">
            <div className="calendar-event-title">{evt.summary}</div>
            <span className={`calendar-event-status status-${evt.status || "pending"}`}>
              {formatStatusLabel(evt.status)}
            </span>
          </div>
          <div className="calendar-event-time">
            {denseLabel || formatTimeRange(evt, displayTimezone)}
          </div>
          {recurring ? (
            <div className="calendar-event-recurrence" title={evt.rrule}>
              {recurrenceSummary(evt.rrule, evt.timezone)}
            </div>
          ) : null}
          {evt.recurrence_import_warning ? (
            <div
              className="calendar-event-recurrence-warning"
              title={evt.recurrence_import_warning}
            >
              Imported exception dates need review
            </div>
          ) : null}
        </div>
        <div className="calendar-event-actions">
          {paused ? (
            <button
              type="button"
              onClick={() => handleSetEventStatus(evt, "scheduled")}
              disabled={isBusy}
              title="Resume future runs in this series"
            >
              {busyStatusEventId === eventId ? "Updating..." : "Resume series"}
            </button>
          ) : cleared ? (
            <button
              type="button"
              onClick={() => handleSetEventStatus(evt, "pending")}
              disabled={isBusy}
              title="Move this event back into the active list"
            >
              {busyStatusEventId === eventId ? "Updating..." : "Reopen"}
            </button>
          ) : recurring ? (
            <>
              <button
                type="button"
                onClick={() => handleSetEventStatus(evt, "paused")}
                disabled={isBusy}
                title="Pause future runs without deleting the series"
              >
                {busyStatusEventId === eventId ? "Updating..." : "Pause series"}
              </button>
              <button
                type="button"
                onClick={() => handleSetEventStatus(evt, "acknowledged")}
                disabled={isBusy}
                title="End future occurrences without stopping a run already in progress"
              >
                End future series
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => handleSetEventStatus(evt, "acknowledged")}
                disabled={isBusy}
                title="Mark complete without deleting the event"
              >
                {busyStatusEventId === eventId ? "Updating..." : "Done"}
              </button>
              <button
                type="button"
                onClick={() => handleSetEventStatus(evt, "skipped")}
                disabled={isBusy}
                title="Hide this from the active list without deleting it"
              >
                Skip
              </button>
            </>
          )}
          {hasCurrentRun ? (
            <Link
              className="calendar-event-activity-link"
              to="/work-history"
              title="Open Activity to request a cooperative stop for the current run"
            >
              Request current run stop
            </Link>
          ) : null}
          <button
            type="button"
            onClick={() => handleEditEvent(evt)}
            disabled={isBusy}
            title="Edit event"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => handleDeleteEvent(eventId)}
            disabled={isBusy}
            title="Delete event"
          >
            {busyDeleteEventId === eventId ? "Deleting..." : "Delete"}
          </button>
        </div>
      </li>
    );
  };

  return (
    <div className="calendar-tab">
      <FilterBar
        searchPlaceholder="Filter events..."
        searchValue={evtQ}
        onSearch={setEvtQ}
        right={
          <span className="inline-flex" style={{ alignItems: "center", gap: 8 }}>
            <button
              type="button"
              onClick={rehydrateRag}
              disabled={ragBusy}
              title="Memorize stored calendar events into the knowledge base for semantic search"
            >
              {ragBusy ? "memorizing..." : "memorize to knowledge"}
            </button>
            {ragStatus && (
              <span className="status-note">
                {typeof ragStatus.reindexed === "number"
                  ? `memorized ${ragStatus.reindexed}`
                  : ragStatus.error || "updated"}
              </span>
            )}
          </span>
        }
      />
      <div className="calendar-workflow-note">
        <span>
          Calendar shows when work will run. Current-session work stays in Agent
          Console; completed run receipts stay in Activity.
        </span>
        <Link to="/work-history">Open Activity</Link>
      </div>
      <div className="calendar">
        <div className="calendar-header">
          <button onClick={prevMonth} type="button" aria-label="Previous month">
            &lt;
          </button>
          <span>
            {viewMode === "month"
              ? currentMonth.toLocaleString("default", {
                  month: "long",
                  year: "numeric",
                })
              : weekLabel()}
          </span>
          <button onClick={nextMonth} type="button" aria-label="Next month">
            &gt;
          </button>
        </div>
        <div className="calendar-view-toggle">
          <button
            className={viewMode === "month" ? "active" : ""}
            onClick={() => setViewMode("month")}
            type="button"
            aria-current={viewMode === "month" ? "page" : undefined}
          >
            month
          </button>
          <button
            className={viewMode === "week" ? "active" : ""}
            onClick={() => setViewMode("week")}
            type="button"
            aria-current={viewMode === "week" ? "page" : undefined}
          >
            week
          </button>
          <button onClick={goToday} type="button" title="Jump to today">
            today
          </button>
        </div>
        {viewMode === "month" ? (
          <>
            <div className="calendar-weekdays">
              {days.map((d) => (
                <div key={d}>{d}</div>
              ))}
            </div>
            {loading ? (
              <div style={{ padding: 12 }}>
                <Line width="30%" />
                <Rect height={220} />
              </div>
            ) : (
              <div className="calendar-grid">
                {dates.map((date) => {
                  const classes = ["calendar-day"];
                  if (date.getMonth() !== currentMonth.getMonth()) {
                    classes.push("other-month");
                  }
                  if (isSameDay(date, now)) {
                    classes.push("today");
                  }
                  if (isSameDay(date, selectedDate)) {
                    classes.push("selected");
                  }
                  return (
                    <div
                      key={date.toISOString()}
                      className={classes.join(" ")}
                      onClick={() => setSelectedDate(date)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(evt) => {
                        if (evt.key === "Enter" || evt.key === " ") {
                          setSelectedDate(date);
                        }
                      }}
                    >
                      {date.getDate()}
                      {eventDates.has(civilDateKey(date)) && (
                        <span className="event-dot" />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        ) : loading ? (
          <div style={{ padding: 12 }}>
            <Line width="40%" />
            <Rect height={240} />
          </div>
        ) : (
          <WeekView
            startDate={selectedDate}
            events={scheduledEventsForWeek}
            onEventClick={handleEditEvent}
            timeZone={displayTimezone}
          />
        )}
      </div>

      <div className="calendar-events-panel">
        <div className="calendar-events-header">
          <h3>
            Events on
            {" "}
            {selectedDate.toLocaleDateString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
            })}
          </h3>
          <div className="calendar-events-actions">
            <button
              type="button"
              onClick={handleRefresh}
              disabled={loading}
              title="Reload events from the server"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={handleNewEvent}
              title="Create a new event"
            >
              New Event
            </button>
          </div>
        </div>
        {(eventError || occurrenceError) && (
          <div className="calendar-error">{eventError || occurrenceError}</div>
        )}
        {loading ? (
          <div style={{ padding: 12 }}>
            <Line width="50%" />
            <Rect height={120} />
          </div>
        ) : eventsForSelectedDay.length === 0 ? (
          <p className="calendar-empty">No events scheduled for this day.</p>
        ) : (
          <>
            {activeEventsForSelectedDay.length ? (
              <ul className="calendar-events-list">
                {activeEventsForSelectedDay.map((evt) => renderEventRow(evt))}
              </ul>
            ) : (
              <p className="calendar-empty">
                No active events scheduled for this day.
              </p>
            )}

            {pausedEventsForSelectedDay.length ? (
              <div className="calendar-paused-section">
                <p className="calendar-cleared-label">
                  Paused series ({pausedEventsForSelectedDay.length})
                </p>
                <ul className="calendar-events-list calendar-events-list-paused">
                  {pausedEventsForSelectedDay.map((evt) => renderEventRow(evt))}
                </ul>
              </div>
            ) : null}

            {clearedEventsForSelectedDay.length ? (
              <div className="calendar-cleared-section">
                <div className="calendar-cleared-header">
                  <p className="calendar-cleared-label">
                    Cleared tasks ({clearedEventsForSelectedDay.length})
                  </p>
                  <button
                    type="button"
                    onClick={() => setShowClearedEvents((prev) => !prev)}
                    title="Toggle completed/skipped events for this day"
                  >
                    {showClearedEvents ? "Hide cleared" : "Show cleared"}
                  </button>
                </div>
                {showClearedEvents ? (
                  <ul className="calendar-events-list calendar-events-list-cleared">
                    {clearedEventsForSelectedDay.map((evt) => renderEventRow(evt))}
                  </ul>
                ) : (
                  <p className="calendar-empty">
                    Completed or skipped tasks stay here until you reopen or delete
                    them.
                  </p>
                )}
              </div>
            ) : null}
          </>
        )}
      </div>

      <EventForm
        key={formOpen ? (activeEvent ? activeEvent.id : "new") : "closed"}
        isOpen={formOpen}
        event={activeEvent}
        selectedDate={selectedDate}
        onSaved={handleEventSaved}
        onCancel={() => {
          setFormOpen(false);
          setActiveEvent(null);
        }}
      />

      <div className="calendar-import">
        <h3>Import</h3>
        <button type="button" onClick={handleGoogleImport}>
          Google Calendar
        </button>
        <input type="file" accept=".ics" onChange={handleICSUpload} />
      </div>
    </div>
  );
};

export default CalendarTab;
