
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import axios from "axios";

const durationPresets = ["all-day", 15, 30, 45, 60, 120];

const normalizeDate = (value) => {
  if (!value) return new Date();
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isNaN(ms) ? new Date() : value;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
};

const toDateInput = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const toTimeInput = (date) => {
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${hour}:${minute}`;
};

const slugify = (input) =>
  input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
    .slice(0, 48);

const TaskComposer = ({
  initialDate,
  onCreated,
  onCancel,
  disabled = false,
  prefill = {},
}) => {
  const defaultTz = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    [],
  );
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [durationMin, setDurationMin] = useState(60);
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const base = normalizeDate(initialDate || prefill.startDate);
    setDate(toDateInput(base));
    setTime(toTimeInput(base));
  }, [initialDate, prefill.startDate]);

  useEffect(() => {
    if (!prefill) return;
    if (prefill.title) setTitle(prefill.title);
    if (prefill.notes) setNotes(prefill.notes);
    if (prefill.location) setLocation(prefill.location);
    if (Number.isFinite(prefill.durationMin)) setDurationMin(prefill.durationMin);
  }, [prefill]);

  const startDate = useMemo(() => {
    if (!date || !time) return null;
    const parsed = new Date(`${date}T${time}`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }, [date, time]);

  const endDate = useMemo(() => {
    if (!startDate) return null;
    if (durationMin === "all-day") {
      return new Date(startDate.getTime() + 24 * 60 * 60000);
    }
    const minutes =
      typeof durationMin === "number" && Number.isFinite(durationMin)
        ? durationMin
        : 60;
    if (minutes <= 0) return new Date(startDate.getTime());
    const safeMinutes = Math.max(5, minutes);
    return new Date(startDate.getTime() + safeMinutes * 60000);
  }, [startDate, durationMin]);

  const nudgeDate = useCallback(
    (days) => {
      const base = startDate || normalizeDate(initialDate);
      const deltaDays = Number.isFinite(days) ? days : 0;
      const shifted = new Date(base.getTime() + deltaDays * 86400000);
      setDate(toDateInput(shifted));
      setTime(toTimeInput(shifted));
    },
    [initialDate, startDate],
  );

  const nudgeTime = useCallback(
    (minutes) => {
      const base = startDate || normalizeDate(initialDate);
      const deltaMinutes = Number.isFinite(minutes) ? minutes : 0;
      const shifted = new Date(base.getTime() + deltaMinutes * 60000);
      setDate(toDateInput(shifted));
      setTime(toTimeInput(shifted));
    },
    [initialDate, startDate],
  );

  const snapToNow = useCallback(() => {
    const now = new Date();
    setDate(toDateInput(now));
    setTime(toTimeInput(now));
  }, []);

  const friendlySummary = useMemo(() => {
    if (!startDate || !endDate) return "";
    const sameDay = startDate.toDateString() === endDate.toDateString();
    const dateOpts = { weekday: "short", month: "short", day: "numeric" };
    const timeOpts = { hour: "2-digit", minute: "2-digit" };
    const startLabel = `${startDate.toLocaleDateString([], dateOpts)} at ${startDate.toLocaleTimeString([], timeOpts)}`;
    const endLabel = sameDay
      ? endDate.toLocaleTimeString([], timeOpts)
      : `${endDate.toLocaleDateString([], dateOpts)} at ${endDate.toLocaleTimeString([], timeOpts)}`;
    return `${startLabel} - ${endLabel}`;
  }, [startDate, endDate]);

  const resetForm = useCallback(() => {
    setTitle("");
    setLocation("");
    setNotes("");
    setDurationMin(60);
    setError("");
  }, []);

  const closeComposer = useCallback(() => {
    resetForm();
    onCancel?.();
  }, [onCancel, resetForm]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (disabled || submitting) return;
    if (!title.trim() || !startDate) {
      setError("Provide a title, date, and time to schedule the event.");
      return;
    }
    try {
      setSubmitting(true);
      setError("");
      const idRoot = slugify(title.trim()) || "task";
      const eventId = `${idRoot}-${startDate.getTime()}`;
      const payload = {
        id: eventId,
        title: title.trim(),
        start_time: Math.floor(startDate.getTime() / 1000),
        end_time: endDate ? Math.floor(endDate.getTime() / 1000) : undefined,
        timezone: defaultTz,
        status: "pending",
        location: location.trim() || undefined,
        description: notes.trim() || undefined,
      };
      await axios.post(`/api/calendar/events/${encodeURIComponent(eventId)}`, payload);
      resetForm();
      onCreated?.(payload);
    } catch (err) {
      console.error("Unable to create event", err);
      setError("Could not create the event. Please try again in a moment.");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeComposer();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeComposer]);

  const handleOverlayClick = (event) => {
    if (event.target === event.currentTarget) {
      closeComposer();
    }
  };

  const content = (
    <div
      className="task-composer-overlay"
      role="presentation"
      onClick={handleOverlayClick}
    >
      <form
        className="task-composer"
        onSubmit={handleSubmit}
        aria-disabled={disabled || submitting}
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="task-composer-close"
          aria-label="Close task composer"
          onClick={closeComposer}
        >
          &times;
        </button>
        <header className="task-composer-header">
          <div>
            <h3>Event builder</h3>
            <p className="task-composer-summary">
              {friendlySummary
                ? `Scheduled for ${friendlySummary}`
                : "Pick a date and time to preview the slot."}
            </p>
          </div>
          <span className="task-composer-timezone">{defaultTz}</span>
        </header>

        <div className="task-composer-grid">
          <label className="task-composer-field span-2">
            <span>Title</span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Walk through Q4 objectives"
              required
            />
          </label>
          <label className="task-composer-field">
            <span>Date</span>
            <div className="task-composer-inline">
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
              />
              <div
                className="task-composer-nudges"
                role="group"
                aria-label="Adjust start date"
              >
                <button type="button" onClick={() => nudgeDate(-1)}>-1d</button>
                <button type="button" onClick={() => nudgeDate(1)}>+1d</button>
              </div>
              <span className="task-composer-weekday">
                {startDate
                  ? startDate.toLocaleDateString([], { weekday: "short" })
                  : "\u2014"}
              </span>
            </div>
          </label>
          <label className="task-composer-field">
            <span>Start time</span>
            <div className="task-composer-inline">
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                required
              />
              <div
                className="task-composer-nudges"
                role="group"
                aria-label="Adjust start time"
              >
                <button type="button" onClick={() => nudgeTime(-15)}>-15m</button>
                <button type="button" onClick={() => nudgeTime(15)}>+15m</button>
                <button type="button" onClick={snapToNow}>Now</button>
              </div>
            </div>
          </label>
          <label className="task-composer-field">
            <span>Location (optional)</span>
            <input
              type="text"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="HQ / Studio"
            />
          </label>
          <label className="task-composer-field span-2">
            <span>Notes (optional)</span>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Outline agenda, docs, or context?"
            />
          </label>
        </div>

        <div className="task-composer-duration">
          <label className="task-composer-field">
            <span>Duration (minutes)</span>
            <input
              type="number"
              min={5}
              max={720}
              step={5}
              value={durationMin === "all-day" ? "" : durationMin}
              placeholder={durationMin === "all-day" ? "all-day" : undefined}
              disabled={durationMin === "all-day"}
              onChange={(e) =>
                setDurationMin(parseInt(e.target.value || "60", 10))
              }
            />
          </label>
          <div
            className="task-composer-duration-chips"
            role="group"
            aria-label="Quick duration presets"
          >
            {durationPresets.map((minutes) => (
              <button
                key={minutes}
                type="button"
                className={
                  durationMin === minutes
                    ? "task-composer-chip active"
                    : "task-composer-chip"
                }
                onClick={() => setDurationMin(minutes)}
              >
                {minutes === "all-day"
                  ? "All day"
                  : minutes === 60
                      ? "1h"
                      : minutes === 120
                        ? "2h"
                        : `${minutes}m`}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="task-composer-error" role="alert">
            {error}
          </p>
        )}

        <div className="task-composer-actions">
          <button
            type="button"
            className="ghost"
            onClick={closeComposer}
            disabled={submitting}
          >
            Cancel
          </button>
          <button type="submit" disabled={disabled || submitting}>
            {submitting ? "Scheduling..." : "Create event"}
          </button>
        </div>
      </form>
    </div>
  );

  if (typeof document === "undefined") return content;
  return createPortal(content, document.body);
};

export default TaskComposer;
