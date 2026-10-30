import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { normalizeToolDisplayMode, normalizeToolLinkBehavior } from "../utils/toolDisplayModes";

const text = (value, fallback) =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;
const boolean = (value, fallback) => typeof value === "boolean" ? value : fallback;
const array = (value, fallback) => Array.isArray(value) ? value : fallback;
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const REQUEST_TIMEOUT_MS = 15000;

// Only these preferences belong to the shared UI autosave. Other settings have
// explicit save actions, and model registrations are hydrated without autosave.
const fields = [
  ["approvalLevel", "approval_level", text],
  ["theme", "theme", text],
  ["visualTheme", "visual_theme", text],
  ["toolDisplayMode", "tool_display_mode", (value, fallback) =>
    typeof value === "string" ? normalizeToolDisplayMode(value) : fallback],
  ["toolLinkBehavior", "tool_link_behavior", (value, fallback) =>
    typeof value === "string" ? normalizeToolLinkBehavior(value) : fallback],
  ["liveTranscriptEnabled", "live_transcript_enabled", boolean],
  ["liveCameraDefaultEnabled", "live_camera_default_enabled", boolean],
  ["workflowProfile", "default_workflow", text],
  ["captureRetentionDays", "capture_retention_days", (value, fallback) =>
    typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : fallback],
  ["captureDefaultSensitivity", "capture_default_sensitivity", text],
  ["captureAllowModelRawImageAccess", "capture_allow_model_raw_image_access", boolean],
  ["captureAllowSummaryFallback", "capture_allow_summary_fallback", boolean],
  ["enabledWorkflowModules", "enabled_workflow_modules", array],
  ["userTimezone", "user_timezone", (value, fallback) =>
    typeof value === "string" ? value : fallback],
  ["registeredLocalModels", "local_model_registrations", array, false],
  ["ragEmbeddingModel", "rag_embedding_model", text, false],
  ["ragClipModel", "rag_clip_model", text, false],
];

const snapshot = (state) => Object.fromEntries(fields.map(([key]) => [key, state[key]]));
const retryDelay = (attempt) => Math.min(1000 * 2 ** Math.min(attempt, 5), 30000);
const patchFor = (current, saved, uncertain) => Object.fromEntries(
  fields
    .filter(([key, apiKey, , writable]) => writable !== false &&
      (uncertain.has(apiKey) || !equal(current[key], saved[key])))
    .map(([key, apiKey]) => [apiKey, current[key]]),
);

export const useUserSettingsSync = (state, setState) => {
  const initialRef = useRef(snapshot(state));
  const currentRef = useRef(initialRef.current);
  const touchedRef = useRef(new Set());
  const savedRef = useRef(null);
  const uncertainRef = useRef(new Set());
  const hydratedRef = useRef(false);
  const savingRef = useRef(false);
  const saveControllerRef = useRef(null);
  const saveAttemptRef = useRef(0);
  const saveRetryAtRef = useRef(0);
  const initialThemesRef = useRef(state.customThemes);
  const [revision, setRevision] = useState(0);
  currentRef.current = snapshot(state);
  const currentKey = JSON.stringify(currentRef.current);

  if (!hydratedRef.current) {
    fields.forEach(([key]) => {
      if (!equal(currentRef.current[key], initialRef.current[key])) touchedRef.current.add(key);
    });
  }

  useEffect(() => {
    let cancelled = false;
    let controller;
    let timer;
    let attempt = 0;
    const load = async () => {
      controller = new AbortController();
      try {
        const { data } = await axios.get("/api/user-settings", {
          signal: controller.signal, timeout: REQUEST_TIMEOUT_MS,
        });
        if (cancelled) return;
        if (!data || typeof data !== "object" || Array.isArray(data) ||
            !fields.some(([, apiKey]) => Object.hasOwn(data, apiKey))) {
          throw new Error("Invalid user settings response");
        }
        const saved = Object.fromEntries(fields.map(([key, apiKey, read]) =>
          [key, read(data[apiKey], initialRef.current[key])],
        ));
        const touched = new Set(touchedRef.current);
        savedRef.current = saved;
        hydratedRef.current = true;
        setState((previous) => {
          if (cancelled) return previous;
          const next = { ...previous };
          fields.forEach(([key]) => {
            // Compare again inside the updater in case another edit was queued
            // after the last render but before this response was applied.
            if (!touched.has(key) && equal(previous[key], initialRef.current[key])) {
              next[key] = saved[key];
            }
          });
          return next;
        });
        setRevision((value) => value + 1);
      } catch {
        if (!cancelled) timer = setTimeout(load, retryDelay(attempt++));
      }
    };
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [setState]);

  useEffect(() => {
    let cancelled = false;
    let controller;
    let timer;
    let attempt = 0;
    const load = async () => {
      controller = new AbortController();
      try {
        const { data } = await axios.get("/api/themes", {
          signal: controller.signal, timeout: REQUEST_TIMEOUT_MS,
        });
        if (cancelled) return;
        if (!Array.isArray(data?.themes)) throw new Error("Invalid theme catalog response");
        setState((previous) => cancelled || !equal(previous.customThemes, initialThemesRef.current)
          ? previous
          : { ...previous, customThemes: data.themes });
      } catch {
        if (!cancelled) timer = setTimeout(load, retryDelay(attempt++));
      }
    };
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [setState]);

  useEffect(() => () => {
    saveControllerRef.current?.abort();
    savingRef.current = false;
  }, []);

  useEffect(() => {
    if (!hydratedRef.current || savingRef.current) return undefined;
    if (!Object.keys(patchFor(currentRef.current, savedRef.current, uncertainRef.current)).length) return undefined;
    const timer = setTimeout(async () => {
      const sent = currentRef.current;
      const patch = patchFor(sent, savedRef.current, uncertainRef.current);
      if (!Object.keys(patch).length) return;
      const controller = new AbortController();
      saveControllerRef.current = controller;
      savingRef.current = true;
      try {
        const { data } = await axios.post("/api/user-settings", patch, {
          signal: controller.signal, timeout: REQUEST_TIMEOUT_MS,
        });
        if (controller.signal.aborted) return;
        if (data?.status !== "saved") throw new Error("Invalid settings save acknowledgement");
        // Serialize writes and acknowledge only what this request sent. Edits
        // made during the request remain dirty and get their own later save.
        fields.forEach(([key, apiKey]) => {
          if (Object.hasOwn(patch, apiKey)) {
            savedRef.current[key] = sent[key];
            uncertainRef.current.delete(apiKey);
          }
        });
        saveAttemptRef.current = 0;
        saveRetryAtRef.current = 0;
      } catch {
        if (!controller.signal.aborted) {
          // A lost response may follow a successful write. Reapply the latest
          // values for those fields even if the user reverted to the baseline.
          Object.keys(patch).forEach((key) => uncertainRef.current.add(key));
          saveRetryAtRef.current = Date.now() + retryDelay(saveAttemptRef.current++);
        }
      } finally {
        if (!controller.signal.aborted) {
          savingRef.current = false;
          setRevision((value) => value + 1);
        }
      }
    }, Math.max(400, saveRetryAtRef.current - Date.now()));
    return () => clearTimeout(timer);
  }, [currentKey, revision]);
};
