import React, { useEffect, useRef, useState } from "react";
import "../styles/ProgressBar.css";

const Notifications = () => {
  const [toasts, setToasts] = useState([]);
  const esRef = useRef(null);
  const addToast = (payload) => {
    const id = `${payload?.ts || Date.now()}-${Math.random().toString(36).slice(2)}`;
    setToasts((prev) => [
      ...prev,
      {
        id,
        title: payload?.title,
        body: payload?.body,
        data: payload?.data || {},
        category: payload?.category || "general",
      },
    ]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  };

  useEffect(() => {
    try {
      fetch("/api/notifications/recent")
        .then((res) => (res.ok ? res.json() : { notifications: [] }))
        .then((payload) => {
          const items = Array.isArray(payload?.notifications)
            ? payload.notifications.slice(-3)
            : [];
          items.forEach((entry) => addToast(entry));
        })
        .catch(() => {});
    } catch {}
    if (typeof EventSource !== "function") {
      return undefined;
    }
    const source = new EventSource("/api/stream/notifications");
    const handler = (evt) => {
      try {
        addToast(JSON.parse(evt.data || "{}"));
      } catch {
        // ignore
      }
    };
    source.addEventListener("notification", handler);
    source.onmessage = handler;
    esRef.current = source;
    return () => {
      try {
        source.close();
      } catch {}
    };
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="download-tray expanded" style={{ pointerEvents: "none" }}>
      <div className="download-tray-content" style={{ pointerEvents: "auto" }}>
        <div className="download-toasts">
          {toasts.map((t) => (
            <div className="download-toast" key={t.id}>
              <div className="download-toast-text">
                <strong>{t.title || "Notification"}</strong>
                {t.body ? ` \u2014 ${t.body}` : ""}
              </div>
              <div className="download-toast-actions">
                {t.data?.action_url && (
                  <a className="dl-btn" href={t.data.action_url} title="Open">
                    Open
                  </a>
                )}
                {t.data?.path && (
                  <a
                    className="dl-btn"
                    href={`file://${t.data.path}`}
                    target="_blank"
                    rel="noreferrer noopener"
                    title="Open folder"
                  >
                    Open folder
                  </a>
                )}
                <button
                  className="dl-btn danger"
                  title="Dismiss"
                  onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
                >
                  {"\u2715"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Notifications;
