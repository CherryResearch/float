import React, { useState } from 'react';

/**
 * Threads module with expandable thread items.
 * Each thread shows a title and metadata preview; clicking expands to reveal
 * the thread content.
 */
const ThreadsModule = ({ threads = [] }) => {
  const [openId, setOpenId] = useState(null);
  const toggle = (id) => setOpenId((prev) => (prev === id ? null : id));

  return (
    <div style={{ maxHeight: 200, overflowY: 'auto' }}>
      {threads.map((t) => (
        <div key={t.id}>
          <button onClick={() => toggle(t.id)}>{t.title}</button>
          {t.metadata && (
            <span style={{ marginLeft: 8, color: 'var(--color-text-muted)' }}>
              {t.metadata}
            </span>
          )}
          {openId === t.id && (
            <div data-testid="thread-details" style={{ marginTop: 4 }}>
              {t.content}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default ThreadsModule;

