import React, { useState } from "react";
import "../styles/WeekView.css";
import FilterBar from "./FilterBar";
import {
  civilDateKey,
  dateKeyInTimeZone,
  hourInTimeZone,
} from "../utils/zonedDateTime";

const WeekView = ({ startDate, events = [], onEventClick, timeZone = "" }) => {
  const weekStart = new Date(startDate);
  weekStart.setDate(startDate.getDate() - startDate.getDay());

  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(weekStart.getDate() + i);
    return d;
  });

  const hours = Array.from({ length: 24 }, (_, i) => i);

  const [notes, setNotes] = useState({});
  const [modalInfo, setModalInfo] = useState(null); // { dateStr, hour, noteIndex }
  const [form, setForm] = useState({ text: "", taskId: "" });
  const [noteQ, setNoteQ] = useState("");

  const openModal = (date, hour, noteIndex = null) => {
    const dateStr = civilDateKey(date);
    const note =
      noteIndex !== null && notes[dateStr] ? notes[dateStr][noteIndex] : null;
    setForm({
      text: note ? note.text : "",
      taskId: note && note.taskId ? note.taskId : "",
    });
    setModalInfo({ dateStr, hour, noteIndex });
  };

  const closeModal = () => {
    setModalInfo(null);
    setForm({ text: "", taskId: "" });
  };

  const saveNote = (e) => {
    e.preventDefault();
    const { dateStr, hour, noteIndex } = modalInfo;
    setNotes((prev) => {
      const dayNotes = prev[dateStr] ? [...prev[dateStr]] : [];
      const newNote = { hour, text: form.text };
      if (form.taskId) newNote.taskId = form.taskId;
      if (noteIndex !== null) {
        dayNotes[noteIndex] = newNote;
      } else {
        dayNotes.push(newNote);
      }
      return { ...prev, [dateStr]: dayNotes };
    });
    closeModal();
  };

  const notesForSlot = (dateStr, hour) => {
    const list = (notes[dateStr] || []).filter((n) => n.hour === hour);
    const q = (noteQ || "").toLowerCase();
    if (!q) return list;
    return list.filter((n) => String(n.text || "").toLowerCase().includes(q));
  };

  const eventsForSlot = (date, hour) => {
    const q = (noteQ || "").toLowerCase();
    const grouped = new Map();
    (Array.isArray(events) ? events : []).forEach((event) => {
      const start = event?.startDate instanceof Date ? event.startDate : null;
      if (
        !start ||
        dateKeyInTimeZone(start, timeZone) !== civilDateKey(date) ||
        hourInTimeZone(start, timeZone) !== hour
      ) {
        return;
      }
      const haystack = [event.summary, event.title, event.description]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (q && !haystack.includes(q)) return;
      const key = String(event.source_event_id || event.id || event.occurrence_id);
      const current = grouped.get(key);
      if (current) {
        current.count += 1;
      } else {
        grouped.set(key, { ...event, count: 1 });
      }
    });
    return [...grouped.values()];
  };

  return (
    <div className="week-view">
      <FilterBar
        searchPlaceholder="Filter events or notes…"
        searchValue={noteQ}
        onSearch={setNoteQ}
      />
      <table className="week-table">
        <thead>
          <tr>
            <th></th>
            {days.map((d) => (
              <th key={d.toISOString()}>
                {d.toLocaleDateString(undefined, {
                  weekday: "short",
                  month: "short",
                  day: "numeric",
                })}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {hours.map((h) => (
            <tr key={h}>
              <td className="hour-label">{`${h}:00`}</td>
              {days.map((d) => {
                const dateStr = civilDateKey(d);
                const slotNotes = notesForSlot(dateStr, h);
                const slotEvents = eventsForSlot(d, h);
                return (
                  <td
                    key={dateStr}
                    className="week-cell"
                    onClick={() => openModal(d, h)}
                  >
                    {slotEvents.map((event) => (
                      <button
                        type="button"
                        key={event.occurrence_id || event.source_event_id || event.id}
                        className={`week-event${
                          String(event?.status || "").toLowerCase() === "paused"
                            ? " is-paused"
                            : ""
                        }`}
                        onClick={(clickEvent) => {
                          clickEvent.stopPropagation();
                          onEventClick?.(event);
                        }}
                        title={event.rrule || event.description || event.summary}
                        aria-label={`${event.summary || event.title || event.id}${
                          String(event?.status || "").toLowerCase() === "paused"
                            ? ", paused"
                            : ""
                        }`}
                      >
                        {event.summary || event.title || event.id}
                        {String(event?.status || "").toLowerCase() === "paused"
                          ? " (Paused)"
                          : ""}
                        {event.count > 1 ? ` · ${event.count} runs` : ""}
                      </button>
                    ))}
                    {slotNotes.map((n, i) => (
                      <div
                        key={i}
                        className="note"
                        onClick={(e) => {
                          e.stopPropagation();
                          openModal(d, h, i);
                        }}
                      >
                        {n.text}
                        {n.taskId ? ` (Task: ${n.taskId})` : ""}
                      </div>
                    ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {modalInfo && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              {modalInfo.dateStr} at {modalInfo.hour}:00
            </h3>
            <form onSubmit={saveNote}>
              <input
                type="text"
                placeholder="Note"
                value={form.text}
                onChange={(e) => setForm({ ...form, text: e.target.value })}
                required
              />
              <input
                type="text"
                placeholder="Task ID (optional)"
                value={form.taskId}
                onChange={(e) => setForm({ ...form, taskId: e.target.value })}
              />
              <div className="modal-actions">
                <button type="submit" className="btn-primary">
                  Save
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={closeModal}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default WeekView;
