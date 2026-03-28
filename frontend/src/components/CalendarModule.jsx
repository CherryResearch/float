import React, { useState } from 'react';

/**
 * Calendar module that lists events and shows a pop-up with details
 * when an event is clicked.
 */
const CalendarModule = ({ events = [] }) => {
  const [selected, setSelected] = useState(null);

  return (
    <div>
      <div style={{ maxHeight: 200, overflowY: 'auto' }}>
        {events.map((ev) => (
          <div key={ev.id}>
            <button onClick={() => setSelected(ev)}>{ev.title}</button>
            <span style={{ marginLeft: 8, color: 'var(--color-text-muted)' }}>
              {ev.date}
            </span>
          </div>
        ))}
      </div>
      {selected && (
        <div data-testid="event-popup" className="calendar-popup" style={{ border: '1px solid var(--color-border)', padding: 8, marginTop: 8 }}>
          <div>{selected.title}</div>
          <div>{selected.description}</div>
          <button onClick={() => setSelected(null)}>close</button>
        </div>
      )}
    </div>
  );
};

export default CalendarModule;

