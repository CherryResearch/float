import React, { useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import axios from "axios";
import ActionListEditor from "./ActionListEditor";
import { GlobalContext } from "../main";

const toLocalInputValue = (date) => {
  if (!date || Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
};

const fromInputValue = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
};

const slugify = (value) =>
  (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);

const getNoteValue = (notes, key) =>
  Array.isArray(notes)
    ? notes.find((note) => note?.id === key)?.content || ""
    : "";

const getReviewInput = (notes) => {
  const raw = getNoteValue(notes, "review");
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "";
  return toLocalInputValue(parsed);
};

const timezones = (() => {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"];
  }
})();

const EventForm = ({
  event,
  selectedDate,
  isOpen,
  onSaved,
  onCancel,
}) => {
  const globalContext = useContext(GlobalContext);
  const state = globalContext?.state || {};
  const defaultTz = useMemo(() => {
    const preferred =
      typeof state.userTimezone === "string" ? state.userTimezone.trim() : "";
    return preferred || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  }, [state.userTimezone]);

  const anchorDate = useMemo(() => {
    const fallback = new Date();
    if (!selectedDate) return fallback;
    const parsed = new Date(selectedDate);
    if (Number.isNaN(parsed.getTime())) return fallback;
    return parsed;
  }, [selectedDate]);

  const [id, setId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [actions, setActions] = useState([]);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [actionsValidation, setActionsValidation] = useState({
    ok: true,
    errors: [],
  });
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [review, setReview] = useState("");
  const [timezone, setTimezone] = useState(defaultTz);
  const [rrule, setRrule] = useState("");
  const [durationMin, setDurationMin] = useState(60);
  const [status, setStatus] = useState("pending");
  const [endActive, setEndActive] = useState(false);
  const [reviewActive, setReviewActive] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const endInputRef = useRef(null);
  const reviewInputRef = useRef(null);

  const notes = useMemo(
    () => (Array.isArray(event?.notes) ? event.notes : []),
    [event?.notes],
  );
  const timezoneOptions = useMemo(() => {
    const tz = (timezone || "").trim();
    if (!tz) return timezones;
    return timezones.includes(tz) ? timezones : [tz, ...timezones];
  }, [timezone]);
  const isEditing = Boolean(event?.id);

  useEffect(() => {
    if (!isOpen) return;
    const base = new Date(anchorDate);
    base.setMinutes(base.getMinutes(), 0, 0);
    const derivedDuration =
      event?.startDate && event?.endDate
        ? Math.max(
            Math.round(
              (event.endDate.getTime() - event.startDate.getTime()) / 60000,
            ),
            5,
          )
        : 60;
    setId(event?.id || `evt-${Date.now()}`);
    setTitle(event?.title || event?.summary || "");
    setDescription(event?.description || getNoteValue(notes, "description"));
    const existingActions = Array.isArray(event?.actions) ? event.actions : null;
    const legacy = getNoteValue(notes, "actions");
    const nextActions =
      existingActions && existingActions.length
        ? existingActions
        : legacy && legacy.trim()
          ? [{ kind: "prompt", prompt: legacy.trim(), status: "scheduled" }]
          : [];
    setActions(nextActions);
    setActionsOpen(Boolean(nextActions.length));
    setActionsValidation({ ok: true, errors: [] });
    setStart(
      event?.startDate
        ? toLocalInputValue(event.startDate)
        : toLocalInputValue(base),
    );
    setEnd(event?.endDate ? toLocalInputValue(event.endDate) : "");
    setReview(getReviewInput(notes));
    setEndActive(Boolean(event?.endDate));
    setReviewActive(Boolean(getReviewInput(notes)));
    setTimezone(event?.timezone || defaultTz);
    setRrule(event?.rrule || "");
    setDurationMin(derivedDuration);
    setStatus(event?.status || "pending");
    setShowAdvanced(false);
    setError("");
  }, [event, notes, defaultTz, anchorDate, isOpen]);

  useEffect(() => {
    if (!isOpen || isEditing) return;
    setId((prev) => {
      const slug = slugify(title);
      if (!slug) return prev || `draft-${Date.now()}`;
      if (!prev || prev.startsWith("evt-") || prev.startsWith("draft-")) {
        return slug;
      }
      return prev;
    });
  }, [title, isEditing, isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handler = (evt) => {
      if (evt.key === "Escape") {
        evt.stopPropagation();
        if (onCancel) onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onCancel]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [isOpen]);

  useEffect(() => {
    if (endActive && endInputRef.current) {
      endInputRef.current.focus();
    }
  }, [endActive]);

  useEffect(() => {
    if (reviewActive && reviewInputRef.current) {
      reviewInputRef.current.focus();
    }
  }, [reviewActive]);

  const handleOverlayClick = (evt) => {
    if (evt.target === evt.currentTarget && onCancel) {
      onCancel();
    }
  };

  const handleSubmit = async (eventObj) => {
    eventObj.preventDefault();
    if (!title.trim()) {
      setError("Please provide an event name.");
      return;
    }
    const startDate = fromInputValue(start);
    if (!startDate) {
      setError("Start time is invalid.");
      return;
    }
    let endDate = fromInputValue(end);
    if ((!end || !endDate) && durationMin > 0) {
      endDate = new Date(startDate.getTime() + durationMin * 60000);
    }
    const reviewDate = review ? fromInputValue(review) : null;
    const baseNotes = Array.isArray(event?.notes) ? [...event.notes] : [];
    const filteredNotes = baseNotes.filter(
      (note) => note && !["description", "actions", "review"].includes(note.id),
    );
    const nowTs = Math.floor(Date.now() / 1000);
    const upsertNote = (noteId, content) => {
      if (!content) return;
      filteredNotes.push({
        id: noteId,
        content,
        timestamp: nowTs,
      });
    };
    if (!actionsValidation.ok) {
      setError(actionsValidation.errors?.[0] || "Fix action validation errors.");
      return;
    }
    if (reviewDate) {
      upsertNote("review", reviewDate.toISOString());
    }
    const payload = {
      id: id || slugify(title) || `evt-${Date.now()}`,
      title: title.trim(),
      description: description.trim() || undefined,
      actions: Array.isArray(actions) ? actions : [],
      start_time: Math.floor(startDate.getTime() / 1000),
      end_time: endDate ? Math.floor(endDate.getTime() / 1000) : undefined,
      rrule: rrule || undefined,
      timezone,
      status,
      notes: filteredNotes,
    };
    setSubmitting(true);
    setError("");
    try {
      await axios.post(
        `/api/calendar/events/${encodeURIComponent(payload.id)}`,
        payload,
      );
      if (onSaved) {
        await onSaved(payload);
      }
    } catch (err) {
      console.error("Failed to save calendar event", err);
      setError("Unable to save. Please verify the details and try again.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  const content = (
    <div
      className="event-popup-overlay"
      role="presentation"
      onClick={handleOverlayClick}
    >
      <form
        className="event-popup"
        role="dialog"
        aria-modal="true"
        onSubmit={handleSubmit}
        onClick={(evt) => evt.stopPropagation()}
      >
        <button
          type="button"
          className="event-popup-close"
          aria-label="Close event editor"
          onClick={onCancel}
        >
          ×
        </button>
        <div className="event-popup-header">
          <input
            type="text"
            className="event-popup-title"
            placeholder="Event name"
            value={title}
            onChange={(evt) => setTitle(evt.target.value)}
            autoFocus
          />
          <textarea
            className="event-popup-description"
            placeholder="Description, intent, or notes…"
            value={description}
            onChange={(evt) => setDescription(evt.target.value)}
          />
        </div>

        <div className="event-popup-body">
          <div className="event-date-column">
            <label className="event-date-chip filled">
              <span>Start</span>
              <input
                type="datetime-local"
                value={start}
                onChange={(evt) => setStart(evt.target.value)}
                required
              />
              <span className="event-date-chip-icon">▾</span>
            </label>

            {endActive ? (
              <label className="event-date-chip filled optional">
                <span>End</span>
                <input
                  ref={endInputRef}
                  type="datetime-local"
                  value={end}
                  onChange={(evt) => setEnd(evt.target.value)}
                />
                <button
                  type="button"
                  className="event-date-chip-action danger"
                  onClick={() => {
                    setEnd("");
                    setEndActive(false);
                  }}
                  aria-label="Clear end"
                >
                  ×
                </button>
              </label>
            ) : (
              <button
                type="button"
                className="event-date-chip ghost"
                onClick={() => setEndActive(true)}
              >
                <span>End</span>
                <span className="event-date-chip-action">+</span>
              </button>
            )}

            {reviewActive ? (
              <label className="event-date-chip filled optional">
                <span>Review</span>
                <input
                  ref={reviewInputRef}
                  type="datetime-local"
                  value={review}
                  onChange={(evt) => setReview(evt.target.value)}
                />
                <button
                  type="button"
                  className="event-date-chip-action danger"
                  onClick={() => {
                    setReview("");
                    setReviewActive(false);
                  }}
                  aria-label="Clear review date"
                >
                  ×
                </button>
              </label>
            ) : (
              <button
                type="button"
                className="event-date-chip ghost"
                onClick={() => setReviewActive(true)}
              >
                <span>Review</span>
                <span className="event-date-chip-action">+</span>
              </button>
            )}

            <div className="event-date-chip actions-chip">
              <span>Actions</span>
              <button
                type="button"
                className="event-date-chip ghost"
                onClick={() => setActionsOpen((prev) => !prev)}
              >
                {actionsOpen ? "Hide" : "Edit"} ({Array.isArray(actions) ? actions.length : 0})
              </button>
            </div>
          </div>

          {actionsOpen && (
            <ActionListEditor
              actions={actions}
              onChange={setActions}
              onValidationChange={setActionsValidation}
              disabled={submitting}
            />
          )}

          <div className="event-popup-guidance">
            <p>
              End/review fields stay hidden until you need them. Use the action
              bubble to outline work a sub-agent should pick up once this event
              fires.
            </p>
            <div className="event-popup-gradient" />
          </div>
        </div>

        <div className="event-popup-footer">
          <button
            type="button"
            className="event-advanced-toggle"
            onClick={() => setShowAdvanced((prev) => !prev)}
          >
            {showAdvanced ? "Hide advanced" : "Advanced settings"}
          </button>
          <button type="submit" disabled={submitting}>
            {submitting
              ? "Saving…"
              : isEditing
                ? "Update event"
                : "Create event"}
          </button>
        </div>

        {showAdvanced && (
          <div className="event-advanced-panel">
            <label>
              <span>Event ID</span>
              <input
                type="text"
                value={id}
                onChange={(evt) => setId(evt.target.value)}
                placeholder="auto-generated"
              />
            </label>
            <label>
              <span>Time zone</span>
              <select
                value={timezone}
                onChange={(evt) => setTimezone(evt.target.value)}
              >
                {timezoneOptions.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Recurrence (RRULE)</span>
              <input
                type="text"
                value={rrule}
                onChange={(evt) => setRrule(evt.target.value)}
                placeholder="FREQ=WEEKLY;INTERVAL=1"
              />
            </label>
            <label>
              <span>Fallback duration (minutes)</span>
              <input
                type="number"
                min={5}
                max={720}
                step={5}
                value={durationMin}
                onChange={(evt) =>
                  setDurationMin(parseInt(evt.target.value || "60", 10))
                }
              />
            </label>
            <label>
              <span>Status</span>
              <select
                value={status}
                onChange={(evt) => setStatus(evt.target.value)}
              >
                <option value="pending">pending</option>
                <option value="scheduled">scheduled</option>
                <option value="prompted">prompted</option>
                <option value="acknowledged">acknowledged</option>
                <option value="skipped">skipped</option>
              </select>
            </label>
          </div>
        )}

        {error && (
          <p className="event-popup-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );

  if (typeof document === "undefined") return content;
  return createPortal(content, document.body);
};

export default EventForm;
