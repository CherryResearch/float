import axios from "axios";

const RAW_API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const API_BASE_URL = RAW_API_BASE.endsWith("/")
  ? RAW_API_BASE.slice(0, -1)
  : RAW_API_BASE;

// Auto-decaying memory store (lightweight client cache)
export const memoryStore = new Proxy(
  {},
  {
    set(target, key, value) {
      console.log(`Memory Updated: ${key}`, value);

      target[key] = {
        data: value,
        timestamp: Date.now(),
        decay: value.importance || 1, // Default importance if not set
      };

      return true;
    },
    get(target, key) {
      if (!target[key]) return null;

      // Decay function
      const timeElapsed = (Date.now() - target[key].timestamp) / 1000;
      target[key].decay *= Math.exp(-0.01 * timeElapsed);

      if (target[key].decay < 0.1) {
        console.log(`Memory Expired: ${key}`);
        delete target[key];
        return null;
      }

      return target[key].data;
    },
  },
);

// Auto-wrapping API calls with optional abort support
export const apiWrapper = new Proxy(
  {},
  {
    get(_, endpoint) {
      return async (params = {}, options = {}) => {
        const { signal } = options || {};
        console.log(`API Call: ${endpoint}`, params);

        try {
          const res = await axios.post(
            `${API_BASE_URL}/${endpoint}`,
            params,
            signal ? { signal } : undefined,
          );
          return res.data;
        } catch (err) {
          const cancelled =
            (signal && signal.aborted) ||
            err?.code === "ERR_CANCELED" ||
            err?.name === "CanceledError";
          if (cancelled) {
            return { cancelled: true };
          }
          console.error(`API Error (${endpoint}):`, err);
          const status = err?.response?.status;
          const payload = err?.response?.data;
          const detail =
            payload?.detail ||
            payload?.message ||
            payload?.error ||
            (status === 502
              ? "Request failed (502). The backend or dev proxy was unavailable for a moment."
              : err?.message) ||
            "API request failed";
          return { error: detail };
        }
      };
    },
  },
);
