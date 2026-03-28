// Generic in-app + push notification helper
// Sends a BroadcastChannel event so all tabs can render a toast,
// and also POSTs to the backend to trigger Web Push when enabled.

export function notify({ title, body, data = {}, category = "general" }) {
  try {
    fetch("/api/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, body, data, category }),
    }).catch(() => {});
  } catch {}
}
