import React, { useMemo, useState } from "react";
import "../styles/WeekView.css";
import FilterBar from "./FilterBar";

const WeekView = ({ startDate }) => {
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
    const dateStr = date.toISOString().split("T")[0];
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

  return (
    <div className="week-view">
      <FilterBar
        searchPlaceholder="Filter notes…"
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
                const dateStr = d.toISOString().split("T")[0];
                const slotNotes = notesForSlot(dateStr, h);
                return (
                  <td
                    key={dateStr}
                    className="week-cell"
                    onClick={() => openModal(d, h)}
                  >
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
