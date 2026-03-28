import React, { useState } from 'react';

/**
 * Simple history module with scrollable content and expandable items.
 * Each history item shows a title and date preview. Clicking toggles
 * a metadata preview panel.
 */
const HistoryModule = ({ items = [] }) => {
  const [openId, setOpenId] = useState(null);
  const toggle = (id) => setOpenId((prev) => (prev === id ? null : id));

  return (
    <div style={{ maxHeight: 200, overflowY: 'auto' }}>
      {items.map((item) => (
        <div key={item.id}>
          <button onClick={() => toggle(item.id)}>{item.title}</button>
          <span style={{ marginLeft: 8, color: 'var(--color-text-muted)' }}>
            {item.date}
          </span>
          {openId === item.id && (
            <div data-testid="history-details" style={{ marginTop: 4 }}>
              {item.details}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default HistoryModule;

