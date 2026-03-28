import React, { useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import axios from "axios";
import ToolArgsForm from "./ToolArgsForm";
import ActionListEditor from "./ActionListEditor";
import "../styles/ToolEditor.css";
import { GlobalContext } from "../main";
import {
  buildModelGroups,
  DEFAULT_API_MODELS,
  formatLocalRuntimeLabel,
  isLocalRuntimeEntry,
  LOCAL_RUNTIME_ENTRIES,
  SUGGESTED_LOCAL_MODELS,
} from "../utils/modelUtils";

const slugify = (input) =>
  (input || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
    .slice(0, 48);

const normalizeDate = (value) => {
  if (!value) return new Date();
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isNaN(ms) ? new Date() : value;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
};

const toDateInput = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const toTimeInput = (date) => {
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${hour}:${minute}`;
};

const resolveEpochMs = (value) => {
  if (!value) return null;
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isNaN(ms) ? null : ms;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1.1e12 ? value : value * 1000;
  }
  if (typeof value === "string") {
    const parsed = new Date(value);
    const ms = parsed.getTime();
    return Number.isNaN(ms) ? null : ms;
  }
  if (typeof value === "object") {
    if (value.dateTime) return resolveEpochMs(value.dateTime);
    if (value.date) return resolveEpochMs(`${value.date}T00:00:00`);
  }
  return null;
};

const toLocalInputValue = (date) => {
  if (!date || Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
};

const parseLocalInputValue = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const normalizeTaskStatus = (value) => {
  const raw = String(value || "")
    .trim()
    .toLowerCase();
  if (!raw) return "pending";
  if (raw === "proposed") return "scheduled";
  if (["acknowledge", "complete", "completed", "done"].includes(raw)) {
    return "acknowledged";
  }
  if (raw === "skip") return "skipped";
  return raw;
};

const TASK_STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "scheduled", label: "Scheduled" },
  { value: "prompted", label: "Needs review" },
  { value: "acknowledged", label: "Done" },
  { value: "skipped", label: "Skipped" },
];

const normalizeArgs = (args) => {
  if (!args || typeof args !== "object") return {};
  if (Array.isArray(args)) return {};
  return args;
};

const normalizeConversationMode = (value, fallback = "new_chat") => {
  const raw = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "_");
  if (
    ["inline", "current_chat", "current_thread", "same_chat", "same_thread"].includes(raw)
  ) {
    return "inline";
  }
  if (
    ["new", "new_chat", "new_thread", "separate_chat", "separate_thread"].includes(raw)
  ) {
    return "new_chat";
  }
  return fallback;
};

const buildInlineConversationContext = (value) => {
  if (!value || typeof value !== "object") return null;
  const sessionId =
    typeof value.session_id === "string" && value.session_id.trim()
      ? value.session_id.trim()
      : null;
  if (!sessionId) return null;
  const messageId =
    typeof value.message_id === "string" && value.message_id.trim()
      ? value.message_id.trim()
      : null;
  const chainId =
    typeof value.chain_id === "string" && value.chain_id.trim()
      ? value.chain_id.trim()
      : messageId;
  return {
    session_id: sessionId,
    ...(messageId ? { message_id: messageId } : {}),
    ...(chainId ? { chain_id: chainId } : {}),
  };
};

const normalizeTaskActionForSave = (
  action,
  { defaultConversationMode = "new_chat", inlineConversation = null } = {},
) => {
  if (!action || typeof action !== "object" || Array.isArray(action)) {
    return null;
  }
  const normalized = { ...action };
  const conversationMode = normalizeConversationMode(
    normalized.conversation_mode,
    normalized.session_id ? "inline" : defaultConversationMode,
  );
  normalized.conversation_mode = conversationMode;
  if (conversationMode === "inline" && inlineConversation?.session_id) {
    if (!normalized.session_id) {
      normalized.session_id = inlineConversation.session_id;
    }
    if (!normalized.message_id && inlineConversation.message_id) {
      normalized.message_id = inlineConversation.message_id;
    }
    if (!normalized.chain_id && inlineConversation.chain_id) {
      normalized.chain_id = inlineConversation.chain_id;
    }
  } else if (conversationMode !== "inline") {
    delete normalized.session_id;
    delete normalized.message_id;
    delete normalized.chain_id;
  }
  return normalized;
};

const timezones = (() => {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch (err) {
    return [Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"];
  }
})();

const validateArgsAgainstSchema = (schema, args) => {
  if (!schema || schema.type !== "object") return { ok: true };
  const props =
    schema.properties && typeof schema.properties === "object"
      ? schema.properties
      : {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  const missing = required.filter((key) => {
    const value = args?.[key];
    if (value === null || value === undefined) return true;
    if (typeof value === "string" && !value.trim()) return true;
    return false;
  });
  if (missing.length) {
    return {
      ok: false,
      message: `Missing required argument(s): ${missing.join(", ")}`,
    };
  }
  for (const [key, propSchema] of Object.entries(props)) {
    if (!propSchema || typeof propSchema !== "object") continue;
    const value = args?.[key];
    if (value === null || value === undefined) continue;
    const rawType = propSchema.type;
    const expected = Array.isArray(rawType) ? rawType[0] : rawType;
    if (!expected) continue;
    if (expected === "string" && typeof value !== "string") {
      return { ok: false, message: `Argument '${key}' must be a string.` };
    }
    if (
      (expected === "number" || expected === "integer") &&
      (typeof value !== "number" || !Number.isFinite(value))
    ) {
      return { ok: false, message: `Argument '${key}' must be a number.` };
    }
    if (expected === "boolean" && typeof value !== "boolean") {
      return { ok: false, message: `Argument '${key}' must be true/false.` };
    }
    if (expected === "array" && !Array.isArray(value)) {
      return { ok: false, message: `Argument '${key}' must be a list.` };
    }
    if (
      expected === "object" &&
      (typeof value !== "object" || value === null || Array.isArray(value))
    ) {
      return { ok: false, message: `Argument '${key}' must be an object.` };
    }
  }
  return { ok: true };
};

const ToolEditorModal = ({
  open,
  tool,
  schedulePrefill = null,
  mode = "tool", // tool | task
  task = null,
  taskPrefill = null,
  onCancel,
  onSubmit,
  onSchedule,
  onSaveTask,
}) => {
  const isTaskMode = mode === "task";
  const dragRef = useRef({
    active: false,
    startX: 0,
    startY: 0,
    originX: 0,
    originY: 0,
  });
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  const [name, setName] = useState(tool?.name || "");
  const [args, setArgs] = useState(() => normalizeArgs(tool?.args));
  const [argsText, setArgsText] = useState(() =>
    JSON.stringify(normalizeArgs(tool?.args), null, 2),
  );
  const [viewMode, setViewMode] = useState("form"); // form | json
  const [error, setError] = useState("");
  const [specsError, setSpecsError] = useState("");
  const [toolSpecs, setToolSpecs] = useState([]);
  const [toolCatalog, setToolCatalog] = useState([]);
  const [catalogError, setCatalogError] = useState("");
  const [loadingSpecs, setLoadingSpecs] = useState(false);
  const { state } = useContext(GlobalContext);
  const apiModelsAvailable = Array.isArray(state.apiModels) ? state.apiModels : [];
  const apiModelsAvailableSet = useMemo(
    () => new Set(apiModelsAvailable),
    [apiModelsAvailable],
  );
  const apiModelGroups = useMemo(
    () =>
      buildModelGroups({
        defaults: DEFAULT_API_MODELS,
        discovered: apiModelsAvailable,
        current: state.apiModel,
      }),
    [apiModelsAvailable, state.apiModel],
  );
  const localModelOptions = useMemo(() => {
    const base = Array.isArray(SUGGESTED_LOCAL_MODELS)
      ? [...SUGGESTED_LOCAL_MODELS]
      : [];
    if (Array.isArray(LOCAL_RUNTIME_ENTRIES)) {
      base.push(...LOCAL_RUNTIME_ENTRIES);
    }
    const current = state.localModel;
    if (current && !base.includes(current)) {
      return [current, ...base];
    }
    return base;
  }, [state.localModel]);
  const serverModelOptions = useMemo(() => {
    const base = Array.isArray(SUGGESTED_LOCAL_MODELS)
      ? [...SUGGESTED_LOCAL_MODELS]
      : [];
    const current = state.transformerModel;
    if (current && !base.includes(current)) {
      return [current, ...base];
    }
    return base;
  }, [state.transformerModel]);
  const resolveContinueTarget = (modeValue) => {
    const currentMode = (modeValue || state.backendMode || "").toLowerCase();
    if (currentMode === "local") {
      return {
        mode: currentMode,
        model: state.localModel || state.transformerModel || state.apiModel || "",
      };
    }
    if (currentMode === "server") {
      return {
        mode: currentMode,
        model: state.transformerModel || state.apiModel || "",
      };
    }
    if (currentMode === "api") {
      return { mode: currentMode, model: state.apiModel || "" };
    }
    return {
      mode: currentMode || state.backendMode || "api",
      model:
        state.apiModel ||
        state.transformerModel ||
        state.localModel ||
        "",
    };
  };
  const initialContinue = resolveContinueTarget(state.backendMode);
  const [continueMode, setContinueMode] = useState(initialContinue.mode);
  const [continueModel, setContinueModel] = useState(initialContinue.model);
  const normalizedContinueMode = (
    continueMode ||
    initialContinue.mode ||
    state.backendMode ||
    "api"
  ).toLowerCase();
  const continueModelOptions = useMemo(() => {
    if (normalizedContinueMode === "server") {
      return serverModelOptions;
    }
    if (normalizedContinueMode === "local") {
      return localModelOptions;
    }
    return apiModelGroups.all;
  }, [
    apiModelGroups.all,
    localModelOptions,
    normalizedContinueMode,
    serverModelOptions,
  ]);
  const inlineConversationContext = useMemo(
    () =>
      buildInlineConversationContext(taskPrefill) ||
      buildInlineConversationContext(task) ||
      buildInlineConversationContext({
        session_id: state.sessionId,
      }),
    [state.sessionId, task, taskPrefill],
  );
  const defaultTaskConversationMode = inlineConversationContext ? "inline" : "new_chat";
  const defaultScheduleConversationMode =
    state.sessionId && (tool?.id || tool?.status) ? "inline" : "new_chat";

  const defaultTz = useMemo(() => {
    const preferred =
      typeof state.userTimezone === "string" ? state.userTimezone.trim() : "";
    return preferred || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  }, [state.userTimezone]);

  const [taskId, setTaskId] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskNotes, setTaskNotes] = useState("");
  const [taskDate, setTaskDate] = useState("");
  const [taskTime, setTaskTime] = useState("09:00");
  const [taskStatus, setTaskStatus] = useState("pending");
  const [taskTimezone, setTaskTimezone] = useState(defaultTz);
  const [taskDurationMin, setTaskDurationMin] = useState(60);
  const [taskSubmitting, setTaskSubmitting] = useState(false);
  const [taskActions, setTaskActions] = useState([]);
  const [taskActionsValidation, setTaskActionsValidation] = useState({
    ok: true,
    errors: [],
  });

  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleEventId, setScheduleEventId] = useState("");
  const [scheduleTitle, setScheduleTitle] = useState("");
  const [scheduleStart, setScheduleStart] = useState("");
  const [scheduleDurationMin, setScheduleDurationMin] = useState(30);
  const [scheduleTimezone, setScheduleTimezone] = useState(defaultTz);
  const [scheduleLocation, setScheduleLocation] = useState("");
  const [scheduleDescription, setScheduleDescription] = useState("");
  const [schedulePrompt, setSchedulePrompt] = useState("");
  const [scheduleConversationMode, setScheduleConversationMode] = useState(
    defaultScheduleConversationMode,
  );
  const [scheduleAdvancedOpen, setScheduleAdvancedOpen] = useState(false);
  const [scheduleSubmitting, setScheduleSubmitting] = useState(false);

  const scheduleTimezoneOptions = useMemo(() => {
    const tz = (scheduleTimezone || "").trim();
    if (!tz) return timezones;
    return timezones.includes(tz) ? timezones : [tz, ...timezones];
  }, [scheduleTimezone]);

  const taskTimezoneOptions = useMemo(() => {
    const tz = (taskTimezone || "").trim();
    if (!tz) return timezones;
    return timezones.includes(tz) ? timezones : [tz, ...timezones];
  }, [taskTimezone]);

  useEffect(() => {
    if (!continueModelOptions.length) return;
    if (!continueModel || !continueModelOptions.includes(continueModel)) {
      setContinueModel(continueModelOptions[0]);
    }
  }, [continueModel, continueModelOptions]);

  useEffect(() => {
    if (!open) return;
    dragRef.current.active = false;
    setDragging(false);
    setDragOffset({ x: 0, y: 0 });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    if (!dragging) return undefined;

    const onMove = (event) => {
      if (!dragRef.current.active) return;
      const nextX =
        dragRef.current.originX + (event.clientX - dragRef.current.startX);
      const nextY =
        dragRef.current.originY + (event.clientY - dragRef.current.startY);
      setDragOffset({ x: nextX, y: nextY });
    };

    const onUp = () => {
      dragRef.current.active = false;
      setDragging(false);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, open]);

  const beginDrag = (event) => {
    if (event.button !== 0) return;
    if (event.target && event.target.closest?.("button, input, textarea, select")) {
      return;
    }
    event.preventDefault();
    dragRef.current.active = true;
    dragRef.current.startX = event.clientX;
    dragRef.current.startY = event.clientY;
    dragRef.current.originX = dragOffset.x;
    dragRef.current.originY = dragOffset.y;
    setDragging(true);
  };

  useEffect(() => {
    if (!open) return;
    if (isTaskMode) return;
    setName(tool?.name || "");
    const normalized = normalizeArgs(tool?.args);
    setArgs(normalized);
    setArgsText(JSON.stringify(normalized, null, 2));
    setViewMode("form");

    const schedule =
      schedulePrefill && typeof schedulePrefill === "object"
        ? schedulePrefill
        : null;
    const baseTitle =
      (schedule?.title && String(schedule.title)) ||
      `Schedule tool: ${tool?.name || "tool"}`;
    const baseStart =
      parseLocalInputValue(schedule?.start) ||
      (typeof schedule?.start_time === "number"
        ? new Date(schedule.start_time * 1000)
        : schedule?.start_time
          ? new Date(schedule.start_time)
          : new Date());
    const baseDuration =
      Number.isFinite(schedule?.durationMin) ? schedule.durationMin : 30;
    setScheduleEventId(schedule?.event_id ? String(schedule.event_id) : "");
    setScheduleTitle(baseTitle);
    setScheduleStart(toLocalInputValue(baseStart));
    setScheduleDurationMin(baseDuration);
    setScheduleTimezone(schedule?.timezone ? String(schedule.timezone) : defaultTz);
    setScheduleLocation(schedule?.location ? String(schedule.location) : "");
    setScheduleDescription(
      schedule?.description ? String(schedule.description) : "",
    );
    setSchedulePrompt(schedule?.prompt ? String(schedule.prompt) : "");
    setScheduleConversationMode(
      normalizeConversationMode(
        schedule?.conversation_mode,
        defaultScheduleConversationMode,
      ),
    );
    setScheduleOpen(Boolean(schedule?.event_id) || tool?.status === "scheduled");
    setScheduleAdvancedOpen(
      Boolean(
        (schedule?.timezone && String(schedule.timezone) !== defaultTz) ||
          schedule?.location ||
          schedule?.description ||
          schedule?.prompt,
      ),
    );
    setScheduleSubmitting(false);
    setError("");
  }, [
    open,
    isTaskMode,
    tool?.name,
    tool?.args,
    tool?.status,
    schedulePrefill,
    defaultTz,
    defaultScheduleConversationMode,
  ]);

  useEffect(() => {
    if (!open) return;
    if (!isTaskMode) return;
    const baseTask =
      task && typeof task === "object"
        ? task
        : taskPrefill && typeof taskPrefill === "object"
          ? taskPrefill
          : {};
    const idText = baseTask.id ? String(baseTask.id) : "";
    const titleText =
      (baseTask.title && String(baseTask.title)) ||
      (baseTask.summary && String(baseTask.summary)) ||
      "";
    const notesText =
      (baseTask.description && String(baseTask.description)) ||
      (baseTask.notes && String(baseTask.notes)) ||
      "";

    const startMs =
      resolveEpochMs(baseTask.start_time) ||
      resolveEpochMs(baseTask.startDate) ||
      resolveEpochMs(baseTask.start) ||
      resolveEpochMs(baseTask.start?.dateTime) ||
      resolveEpochMs(baseTask.start?.date) ||
      Date.now();
    const endMs =
      resolveEpochMs(baseTask.end_time) ||
      resolveEpochMs(baseTask.endDate) ||
      resolveEpochMs(baseTask.end) ||
      resolveEpochMs(baseTask.end?.dateTime) ||
      resolveEpochMs(baseTask.end?.date) ||
      null;
    const startDateObj = normalizeDate(startMs);

    const durationMinRaw =
      Number.isFinite(baseTask.durationMin) && baseTask.durationMin > 0
        ? baseTask.durationMin
        : Number.isFinite(endMs)
          ? Math.max(5, Math.round((endMs - startDateObj.getTime()) / 60000))
          : 60;

    const statusValue = normalizeTaskStatus(baseTask.status);
    const tzText = baseTask.timezone ? String(baseTask.timezone) : defaultTz;
    const actionsList = Array.isArray(baseTask.actions)
      ? baseTask.actions.filter(
          (item) => item && typeof item === "object" && !Array.isArray(item),
        )
      : [];

    setTaskId(idText);
    setTaskTitle(titleText);
    setTaskNotes(notesText);
    setTaskDate(toDateInput(startDateObj));
    setTaskTime(toTimeInput(startDateObj));
    setTaskStatus(statusValue || "pending");
    setTaskTimezone(tzText || defaultTz);
    setTaskDurationMin(durationMinRaw);
    setTaskSubmitting(false);
    setTaskActions(actionsList);
    setTaskActionsValidation({ ok: true, errors: [] });
    setError("");
  }, [open, isTaskMode, task, taskPrefill, defaultTz]);

  useEffect(() => {
    if (!open) return;
    if (isTaskMode) return;
    let cancelled = false;
    const fetchSpecs = async () => {
      setLoadingSpecs(true);
      setSpecsError("");
      setCatalogError("");
      try {
        const [specsResult, catalogResult] = await Promise.allSettled([
          axios.get("/api/tools/specs"),
          axios.get("/api/tools/catalog"),
        ]);
        if (!cancelled) {
          if (specsResult.status === "fulfilled") {
            const list = Array.isArray(specsResult?.value?.data?.tools)
              ? specsResult.value.data.tools
              : [];
            setToolSpecs(list);
          } else {
            setSpecsError("Tool schemas unavailable (falling back to JSON).");
            setToolSpecs([]);
          }
          if (catalogResult.status === "fulfilled") {
            const list = Array.isArray(catalogResult?.value?.data?.tools)
              ? catalogResult.value.data.tools
              : [];
            setToolCatalog(list);
          } else {
            setCatalogError("Tool capability details unavailable.");
            setToolCatalog([]);
          }
        }
      } finally {
        if (!cancelled) setLoadingSpecs(false);
      }
    };
    fetchSpecs();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  const selectedSpec = useMemo(() => {
    const wanted = (name || tool?.name || "").trim();
    if (!wanted) return null;
    return (
      toolSpecs.find(
        (spec) => spec && typeof spec === "object" && spec.name === wanted,
      ) || null
    );
  }, [name, tool?.name, toolSpecs]);

  const selectedCatalog = useMemo(() => {
    const wanted = (name || tool?.name || "").trim();
    if (!wanted) return null;
    return (
      toolCatalog.find(
        (entry) => entry && typeof entry === "object" && entry.id === wanted,
      ) || null
    );
  }, [name, tool?.name, toolCatalog]);

  const uiHints = useMemo(() => {
    const meta = selectedSpec?.metadata;
    if (!meta || typeof meta !== "object") return {};
    const ui = meta.ui;
    return ui && typeof ui === "object" ? ui : {};
  }, [selectedSpec]);

  const catalogBadges = useMemo(() => {
    if (!selectedCatalog || typeof selectedCatalog !== "object") return [];
    const badges = [];
    if (selectedCatalog.status) {
      badges.push({
        key: "status",
        label: String(selectedCatalog.status),
        tone: selectedCatalog.status === "stub" ? "warning" : "muted",
      });
    }
    if (selectedCatalog.category) {
      badges.push({
        key: "category",
        label: String(selectedCatalog.category),
        tone: "muted",
      });
    }
    if (selectedCatalog.origin) {
      badges.push({
        key: "origin",
        label: String(selectedCatalog.origin),
        tone: "muted",
      });
    }
    return badges;
  }, [selectedCatalog]);

  const runtimeBadges = useMemo(() => {
    const runtime =
      selectedCatalog && typeof selectedCatalog.runtime === "object"
        ? selectedCatalog.runtime
        : null;
    if (!runtime) return [];
    const badges = [];
    if (runtime.executor) badges.push(`executor: ${runtime.executor}`);
    if (runtime.network) badges.push("network");
    if (runtime.filesystem) badges.push("filesystem");
    if (runtime.javascript_aware) badges.push("js-aware");
    return badges;
  }, [selectedCatalog]);

  const sandboxHints = useMemo(() => {
    const sandbox =
      selectedCatalog && typeof selectedCatalog.sandbox === "object"
        ? selectedCatalog.sandbox
        : null;
    if (!sandbox) return [];
    const hints = [];
    const readRoots = Array.isArray(sandbox.read_roots) ? sandbox.read_roots : [];
    const writeRoots = Array.isArray(sandbox.write_roots) ? sandbox.write_roots : [];
    const domains = Array.isArray(sandbox.allowed_domains)
      ? sandbox.allowed_domains
      : [];
    if (readRoots.length) hints.push(`reads: ${readRoots.join(", ")}`);
    if (writeRoots.length) hints.push(`writes: ${writeRoots.join(", ")}`);
    if (domains.length) hints.push(`domains: ${domains.join(", ")}`);
    if (sandbox.javascript_aware === false) hints.push("no JavaScript rendering");
    return hints;
  }, [selectedCatalog]);

  const accessHints = useMemo(() => {
    return Array.isArray(selectedCatalog?.can_access) ? selectedCatalog.can_access : [];
  }, [selectedCatalog]);

  const blockedHints = useMemo(() => {
    return Array.isArray(selectedCatalog?.cannot_access)
      ? selectedCatalog.cannot_access
      : [];
  }, [selectedCatalog]);

  const limitHints = useMemo(() => {
    return Array.isArray(selectedCatalog?.limit_hints)
      ? selectedCatalog.limit_hints
      : [];
  }, [selectedCatalog]);

  useEffect(() => {
    if (!open) return;
    if (viewMode !== "form") return;
    try {
      setArgsText(JSON.stringify(args || {}, null, 2));
    } catch {
      setArgsText("{}");
    }
  }, [args, open, viewMode]);

  const cycleContinueMode = () => {
    const order = ["api", "local", "server"];
    const current =
      order.includes(normalizedContinueMode) && normalizedContinueMode
        ? normalizedContinueMode
        : "api";
    const idx = order.indexOf(current);
    const next = order[(idx + 1) % order.length];
    setContinueMode(next);
  };

  const parseArgs = () => {
    if (isTaskMode) return null;
    try {
      if (viewMode === "form") {
        setError("");
        return normalizeArgs(args);
      }
      const parsed = JSON.parse(argsText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setError("Arguments must be a JSON object.");
        return null;
      }
      setError("");
      return parsed;
    } catch (err) {
      setError("Arguments must be valid JSON.");
      return null;
    }
  };

  const handleSubmit = () => {
    if (isTaskMode) return;
    const parsed = parseArgs();
    if (!parsed) return;
    const validation = validateArgsAgainstSchema(selectedSpec?.parameters, parsed);
    if (!validation.ok) {
      setError(validation.message || "Arguments do not match the tool schema.");
      return;
    }
    onSubmit?.({
      name: (name || tool?.name || "").trim() || tool?.name || "tool",
      args: parsed,
      continueTarget: {
        mode: normalizedContinueMode,
        model: continueModel,
      },
    });
  };

  const handleSchedule = async () => {
    if (isTaskMode) return;
    if (!scheduleOpen) {
      setScheduleOpen(true);
      return;
    }
    if (!onSchedule) return;
    const parsed = parseArgs();
    if (!parsed) return;
    const validation = validateArgsAgainstSchema(selectedSpec?.parameters, parsed);
    if (!validation.ok) {
      setError(validation.message || "Arguments do not match the tool schema.");
      return;
    }
    const startDate = parseLocalInputValue(scheduleStart);
    if (!startDate) {
      setError("Schedule time is invalid.");
      return;
    }
    const titleText =
      (scheduleTitle || `Schedule tool: ${(name || tool?.name || "tool").trim()}`)
        .trim() || `Schedule tool: ${(name || tool?.name || "tool").trim()}`;
    const tzText = (scheduleTimezone || defaultTz || "UTC").trim() || "UTC";
    const durationRaw =
      typeof scheduleDurationMin === "number" && Number.isFinite(scheduleDurationMin)
        ? scheduleDurationMin
        : parseInt(String(scheduleDurationMin || "30"), 10);
    const safeDuration = Math.max(
      5,
      Math.min(24 * 60, Math.round(Number.isFinite(durationRaw) ? durationRaw : 30)),
    );
    const endDate = new Date(startDate.getTime() + safeDuration * 60000);
    const eventId =
      (scheduleEventId || "").trim() ||
      `${slugify(titleText) || "task"}-${startDate.getTime()}`;
    if (!scheduleEventId) {
      setScheduleEventId(eventId);
    }

    setScheduleSubmitting(true);
    setError("");
    try {
      await onSchedule({
        name: (name || tool?.name || "").trim() || tool?.name || "tool",
        args: parsed,
        schedule: {
          event_id: eventId,
          title: titleText,
          description: scheduleDescription.trim() || undefined,
          location: scheduleLocation.trim() || undefined,
          start_time: Math.floor(startDate.getTime() / 1000),
          end_time: Math.floor(endDate.getTime() / 1000),
          timezone: tzText,
          status: "scheduled",
          prompt: schedulePrompt.trim() || undefined,
          conversation_mode: normalizeConversationMode(
            scheduleConversationMode,
            defaultScheduleConversationMode,
          ),
        },
      });
      onCancel?.();
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Unable to schedule tool.";
      setError(String(detail));
    } finally {
      setScheduleSubmitting(false);
    }
  };

  const parseTaskStartDate = () => {
    if (!taskDate || !taskTime) return null;
    const parsed = new Date(`${taskDate}T${taskTime}`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  };

  const nudgeTaskDate = (days) => {
    const base = parseTaskStartDate() || normalizeDate(taskPrefill?.startDate);
    const deltaDays = Number.isFinite(days) ? days : 0;
    const shifted = new Date(base.getTime() + deltaDays * 86400000);
    setTaskDate(toDateInput(shifted));
    setTaskTime(toTimeInput(shifted));
  };

  const nudgeTaskMonth = (months) => {
    const base = parseTaskStartDate() || normalizeDate(taskPrefill?.startDate);
    const delta = Number.isFinite(months) ? months : 0;
    const shifted = new Date(base.getTime());
    shifted.setMonth(shifted.getMonth() + delta);
    setTaskDate(toDateInput(shifted));
    setTaskTime(toTimeInput(shifted));
  };

  const nudgeTaskTime = (minutes) => {
    const base = parseTaskStartDate() || normalizeDate(taskPrefill?.startDate);
    const deltaMinutes = Number.isFinite(minutes) ? minutes : 0;
    const shifted = new Date(base.getTime() + deltaMinutes * 60000);
    setTaskDate(toDateInput(shifted));
    setTaskTime(toTimeInput(shifted));
  };

  const snapTaskToNow = () => {
    const now = new Date();
    setTaskDate(toDateInput(now));
    setTaskTime(toTimeInput(now));
  };

  const handleSaveTask = async () => {
    if (!onSaveTask) return;
    const startDate = parseTaskStartDate();
    if (!taskTitle.trim() || !startDate) {
      setError("Provide a title, date, and time to save the task.");
      return;
    }
    if (!taskActionsValidation.ok) {
      setError(taskActionsValidation.errors?.[0] || "Fix action validation errors.");
      return;
    }

    const idText =
      (taskId || "").trim() ||
      `${slugify(taskTitle.trim()) || "task"}-${startDate.getTime()}`;
    const tzText = (taskTimezone || defaultTz || "UTC").trim() || "UTC";
    const durationRaw =
      typeof taskDurationMin === "number" && Number.isFinite(taskDurationMin)
        ? taskDurationMin
        : parseInt(String(taskDurationMin || "60"), 10);
    const safeDuration = Math.max(
      5,
      Math.min(24 * 60, Math.round(Number.isFinite(durationRaw) ? durationRaw : 60)),
    );
    const endDate = new Date(startDate.getTime() + safeDuration * 60000);

    setTaskSubmitting(true);
    setError("");
    try {
      await onSaveTask({
        id: idText,
        title: taskTitle.trim(),
        description: taskNotes.trim() || undefined,
        actions: (Array.isArray(taskActions) ? taskActions : [])
          .map((action) =>
            normalizeTaskActionForSave(action, {
              defaultConversationMode: defaultTaskConversationMode,
              inlineConversation: inlineConversationContext,
            }),
          )
          .filter(Boolean),
        start_time: Math.floor(startDate.getTime() / 1000),
        end_time: Math.floor(endDate.getTime() / 1000),
        timezone: tzText,
        status: normalizeTaskStatus(taskStatus),
      });
      onCancel?.();
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Unable to save task.";
      setError(String(detail));
    } finally {
      setTaskSubmitting(false);
    }
  };

  if (!open) return null;

  if (isTaskMode) {
    const taskStatusLabel =
      TASK_STATUS_OPTIONS.find((option) => option.value === normalizeTaskStatus(taskStatus))
        ?.label || "Pending";
    const content = (
      <div
        className="tool-editor-overlay"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            onCancel?.();
          }
        }}
      >
        <section
          className="tool-editor"
          role="dialog"
          aria-modal="true"
          aria-labelledby="tool-editor-title"
          onClick={(event) => event.stopPropagation()}
          style={{
            transform:
              dragOffset.x || dragOffset.y
                ? `translate(${dragOffset.x}px, ${dragOffset.y}px)`
                : undefined,
          }}
        >
          <header className="tool-editor-header" onMouseDown={beginDrag}>
            <div>
              <p className="tool-editor-label">Task editor</p>
              <h3 id="tool-editor-title" className="tool-editor-title">
                {taskId ? "Edit task" : "New task"}
              </h3>
              <p className="tool-editor-meta">
                {(taskId && `event ${taskId}`) ||
                  (taskStatus && `status ${taskStatusLabel}`) ||
                  "quick task"}
              </p>
            </div>
            <button
              type="button"
              className="tool-editor-close"
              aria-label="Close task editor"
              onClick={onCancel}
            >
              &times;
            </button>
          </header>

          <div className="tool-editor-body">
            <div className="tool-editor-grid">
              <label className="tool-field">
                <span>Title</span>
                <input
                  type="text"
                  value={taskTitle}
                  onChange={(event) => setTaskTitle(event.target.value)}
                  placeholder="Follow up on Q4 roadmap"
                  autoFocus
                />
              </label>

              <div className="tool-schedule-grid task-editor-grid" aria-label="Task schedule">
                <label className="tool-field">
                  <span>Date</span>
                  <div className="tool-inline-row">
                    <input
                      type="date"
                      value={taskDate}
                      onChange={(event) => setTaskDate(event.target.value)}
                    />
                    <div className="tool-nudges" role="group" aria-label="Adjust date">
                      <button
                        type="button"
                        onClick={() => nudgeTaskDate(-1)}
                        title="Move date back 1 day"
                      >
                        -1d
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskDate(-7)}
                        title="Move date back 1 week"
                      >
                        -1w
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskMonth(-1)}
                        title="Move date back 1 month"
                      >
                        -1mo
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskDate(1)}
                        title="Move date forward 1 day"
                      >
                        +1d
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskDate(7)}
                        title="Move date forward 1 week"
                      >
                        +1w
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskMonth(1)}
                        title="Move date forward 1 month"
                      >
                        +1mo
                      </button>
                    </div>
                  </div>
                </label>

                <label className="tool-field">
                  <span>Time</span>
                  <div className="tool-inline-row">
                    <input
                      type="time"
                      value={taskTime}
                      onChange={(event) => setTaskTime(event.target.value)}
                    />
                    <div className="tool-nudges" role="group" aria-label="Adjust time">
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(-1)}
                        title="Move time back 1 minute"
                      >
                        -1m
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(-5)}
                        title="Move time back 5 minutes"
                      >
                        -5m
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(-15)}
                        title="Move time back 15 minutes"
                      >
                        -15m
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(1)}
                        title="Move time forward 1 minute"
                      >
                        +1m
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(5)}
                        title="Move time forward 5 minutes"
                      >
                        +5m
                      </button>
                      <button
                        type="button"
                        onClick={() => nudgeTaskTime(15)}
                        title="Move time forward 15 minutes"
                      >
                        +15m
                      </button>
                      <button
                        type="button"
                        onClick={snapTaskToNow}
                        title="Set date/time to now"
                      >
                        Now
                      </button>
                    </div>
                  </div>
                </label>

                <label className="tool-field">
                  <span>Time zone</span>
                  <select
                    value={taskTimezone}
                    onChange={(event) => setTaskTimezone(event.target.value)}
                  >
                    {taskTimezoneOptions.map((tz) => (
                      <option key={tz} value={tz}>
                        {tz}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="tool-field">
                <span>Status</span>
                <select
                  value={taskStatus}
                  onChange={(event) => setTaskStatus(event.target.value)}
                >
                  {TASK_STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="tool-field">
                <span>Notes (optional)</span>
                <textarea
                  rows={4}
                  value={taskNotes}
                  onChange={(event) => setTaskNotes(event.target.value)}
                  placeholder="Context, links, what success looks like…"
                />
              </label>

              <ActionListEditor
                actions={taskActions}
                onChange={setTaskActions}
                onValidationChange={setTaskActionsValidation}
                disabled={taskSubmitting}
                defaultConversationMode={defaultTaskConversationMode}
                inlineConversation={inlineConversationContext}
              />
            </div>

            {error && (
              <p className="tool-editor-error" role="alert">
                {error}
              </p>
            )}
          </div>

          <div className="tool-editor-actions">
            <button type="button" className="ghost" onClick={onCancel}>
              Cancel
            </button>
            <button type="button" onClick={handleSaveTask} disabled={taskSubmitting}>
              {taskSubmitting ? "Saving..." : taskId ? "Save" : "Create"}
            </button>
          </div>
        </section>
      </div>
    );

    return createPortal(content, document.body);
  }

  const content = (
    <div
      className="tool-editor-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onCancel?.();
        }
      }}
    >
      <section
        className="tool-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tool-editor-title"
        onClick={(event) => event.stopPropagation()}
        style={{
          transform:
            dragOffset.x || dragOffset.y
              ? `translate(${dragOffset.x}px, ${dragOffset.y}px)`
              : undefined,
        }}
      >
        <header className="tool-editor-header" onMouseDown={beginDrag}>
          <div>
            <p className="tool-editor-label">Tool editor</p>
            <h3 id="tool-editor-title" className="tool-editor-title">
              {tool?.name || "tool"}
            </h3>
            <p className="tool-editor-meta">
              {(tool?.id && `request ${tool.id}`) ||
                (tool?.status && tool.status) ||
                "proposed call"}
            </p>
          </div>
          <button
            type="button"
            className="tool-editor-close"
            aria-label="Close tool editor"
            onClick={onCancel}
          >
            &times;
          </button>
        </header>

        <div className="tool-editor-body">
          <div className="tool-editor-grid">
            <label className="tool-field">
              <span>Tool name</span>
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                list="tool-editor-tool-list"
                placeholder={loadingSpecs ? "Loading tools..." : "e.g., search_web"}
              />
              <datalist id="tool-editor-tool-list">
                {toolSpecs
                  .filter((spec) => spec && typeof spec === "object" && spec.name)
                  .map((spec) => (
                    <option key={spec.name} value={spec.name}>
                      {spec.description || spec.name}
                    </option>
                  ))}
              </datalist>
              {specsError && (
                <small className="tool-editor-hint" role="status">
                  {specsError}
                </small>
              )}
            </label>
            {selectedCatalog && (
              <section className="tool-catalog-card" aria-label="Tool capability details">
                <div className="tool-catalog-header">
                  <div className="tool-catalog-copy">
                    <p className="tool-catalog-label">Capability</p>
                    <p className="tool-catalog-summary">
                      {selectedCatalog.summary ||
                        selectedCatalog.description ||
                        selectedSpec?.description ||
                        "No capability details available."}
                    </p>
                  </div>
                  {catalogBadges.length > 0 && (
                    <div className="tool-catalog-badges">
                      {catalogBadges.map((badge) => (
                        <span
                          key={badge.key}
                          className={`tool-catalog-badge tool-catalog-badge--${badge.tone}`}
                        >
                          {badge.label}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {selectedCatalog.description &&
                  selectedCatalog.description !== selectedCatalog.summary && (
                    <p className="tool-catalog-description">
                      {selectedCatalog.description}
                    </p>
                  )}
                {runtimeBadges.length > 0 && (
                  <div className="tool-catalog-row">
                    <span>Runtime</span>
                    <div className="tool-catalog-chip-row">
                      {runtimeBadges.map((hint) => (
                        <span key={hint} className="tool-catalog-chip">
                          {hint}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {sandboxHints.length > 0 && (
                  <div className="tool-catalog-row">
                    <span>Sandbox</span>
                    <ul className="tool-catalog-list">
                      {sandboxHints.map((hint) => (
                        <li key={hint}>{hint}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {accessHints.length > 0 && (
                  <div className="tool-catalog-row">
                    <span>Can access</span>
                    <ul className="tool-catalog-list">
                      {accessHints.map((hint) => (
                        <li key={hint}>{hint}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {blockedHints.length > 0 && (
                  <div className="tool-catalog-row">
                    <span>Cannot access</span>
                    <ul className="tool-catalog-list">
                      {blockedHints.map((hint) => (
                        <li key={hint}>{hint}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {limitHints.length > 0 && (
                  <div className="tool-catalog-row">
                    <span>Limits</span>
                    <ul className="tool-catalog-list">
                      {limitHints.map((hint) => (
                        <li key={hint}>{hint}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}
            {catalogError && !selectedCatalog && (
              <small className="tool-editor-hint" role="status">
                {catalogError}
              </small>
            )}
            <div className="tool-field tool-args-field">
              <div className="tool-args-header">
                <span>Arguments</span>
                <div className="tool-args-view-toggle" role="group" aria-label="Arguments view">
                  <button
                    type="button"
                    className={viewMode === "form" ? "active" : ""}
                    onClick={() => {
                      setViewMode("form");
                      setError("");
                    }}
                  >
                    Form
                  </button>
                  <button
                    type="button"
                    className={viewMode === "json" ? "active" : ""}
                    onClick={() => {
                      setViewMode("json");
                      try {
                        setArgsText(JSON.stringify(normalizeArgs(args), null, 2));
                      } catch {
                        setArgsText("{}");
                      }
                      setError("");
                    }}
                  >
                    JSON
                  </button>
                </div>
              </div>

              {viewMode === "form" ? (
                <ToolArgsForm
                  schema={selectedSpec?.parameters}
                  ui={uiHints}
                  value={args}
                  onChange={(next) => setArgs(next)}
                />
              ) : (
                <textarea
                  rows={10}
                  value={argsText}
                  onChange={(event) => {
                    const nextText = event.target.value;
                    setArgsText(nextText);
                    try {
                      const parsed = JSON.parse(nextText || "{}");
                      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                        setArgs(parsed);
                        setError("");
                      }
                    } catch {
                      // keep last valid args, show error only on submit
                    }
                  }}
                  spellCheck={false}
                />
              )}
            </div>
            {onSubmit && !isTaskMode && (
              <div className="tool-field tool-continue-field">
                <span>Continue with</span>
                <div className="tool-continue-controls">
                  <button
                    type="button"
                    className="chip backend-chip"
                    onClick={cycleContinueMode}
                    aria-label="Continue mode"
                    title="Continue mode: click to cycle api -> local -> cloud"
                  >
                    {normalizedContinueMode === "server"
                      ? "cloud"
                      : normalizedContinueMode}
                  </button>
                  <select
                    className="model-select"
                    aria-label="Continue model"
                    value={continueModel}
                    onChange={(event) => setContinueModel(event.target.value)}
                  >
                    {normalizedContinueMode === "api" ? (
                      <>
                        <optgroup label="defaults">
                          {apiModelGroups.defaults.map((model) => {
                            const disabled =
                              apiModelsAvailableSet.size > 0 &&
                              !apiModelsAvailableSet.has(model);
                            const label = disabled ? `${model} (unavailable)` : model;
                            return (
                              <option key={model} value={model} disabled={disabled}>
                                {label}
                              </option>
                            );
                          })}
                        </optgroup>
                        {apiModelGroups.extras.length > 0 && (
                          <optgroup
                            label={`available${apiModelsAvailable.length ? ` (${apiModelsAvailable.length})` : ""}`}
                          >
                            {apiModelGroups.extras.map((model) => (
                              <option key={model} value={model}>
                                {model}
                              </option>
                            ))}
                          </optgroup>
                        )}
                      </>
                    ) : (
                      continueModelOptions.map((model) => (
                        <option key={model} value={model}>
                          {normalizedContinueMode === "local" &&
                          isLocalRuntimeEntry(model)
                            ? formatLocalRuntimeLabel(model)
                            : model}
                        </option>
                      ))
                    )}
                  </select>
                </div>
                <small className="tool-editor-hint">
                  Select where the assistant continues after this tool runs.
                </small>
              </div>
            )}
          </div>


        {onSchedule && scheduleOpen && (
          <div className="tool-schedule-panel" aria-label="Schedule tool">
            <div className="tool-schedule-header">
              <h4>Schedule</h4>
              <button
                type="button"
                className="tool-schedule-advanced-toggle"
                onClick={() => setScheduleAdvancedOpen((prev) => !prev)}
                aria-expanded={scheduleAdvancedOpen}
              >
                {scheduleAdvancedOpen ? "Hide advanced" : "Advanced settings"}
              </button>
            </div>
            <div className="tool-schedule-grid">
              <label className="tool-field">
                <span>Event name</span>
                <input
                  type="text"
                  value={scheduleTitle}
                  onChange={(event) => setScheduleTitle(event.target.value)}
                  placeholder={`Schedule tool: ${name || tool?.name || "tool"}`}
                />
              </label>
              <label className="tool-field">
                <span>When</span>
                <input
                  type="datetime-local"
                  value={scheduleStart}
                  onChange={(event) => setScheduleStart(event.target.value)}
                />
              </label>
              <label className="tool-field">
                <span>Duration (minutes)</span>
                <input
                  type="number"
                  min={5}
                  max={720}
                  step={5}
                  value={scheduleDurationMin}
                  onChange={(event) =>
                    setScheduleDurationMin(parseInt(event.target.value || "30", 10))
                  }
                />
              </label>
              {scheduleAdvancedOpen && (
                <>
                  <label className="tool-field">
                    <span>Time zone</span>
                    <select
                      value={scheduleTimezone}
                      onChange={(event) => setScheduleTimezone(event.target.value)}
                    >
                      {scheduleTimezoneOptions.map((tz) => (
                        <option key={tz} value={tz}>
                          {tz}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="tool-field">
                    <span>Location (optional)</span>
                    <input
                      type="text"
                      value={scheduleLocation}
                      onChange={(event) => setScheduleLocation(event.target.value)}
                      placeholder="HQ / Studio"
                    />
                  </label>
                  <label className="tool-field">
                    <span>Details (optional)</span>
                    <textarea
                      rows={3}
                      value={scheduleDescription}
                      onChange={(event) => setScheduleDescription(event.target.value)}
                      placeholder="Notes attached to the calendar event..."
                    />
                  </label>
                  <label className="tool-field tool-schedule-prompt">
                    <span>Prompt at run time (optional)</span>
                    <textarea
                      rows={3}
                      value={schedulePrompt}
                      onChange={(event) => setSchedulePrompt(event.target.value)}
                      placeholder="What should Float do after the tool runs?"
                    />
                  </label>
                  <label className="tool-field">
                    <span>Run response in</span>
                    <select
                      value={scheduleConversationMode}
                      onChange={(event) =>
                        setScheduleConversationMode(
                          normalizeConversationMode(
                            event.target.value,
                            defaultScheduleConversationMode,
                          ),
                        )
                      }
                    >
                      <option value="inline" disabled={!state.sessionId}>
                        Current chat
                      </option>
                      <option value="new_chat">New chat</option>
                    </select>
                    <small className="tool-editor-hint">
                      Choose whether the scheduled follow-up continues here or opens its own task chat.
                    </small>
                  </label>
                </>
              )}
            </div>
          </div>
        )}

        {error && (
          <p className="tool-editor-error" role="alert">
            {error}
          </p>
        )}

        </div>

        <div className="tool-editor-actions">
          <button type="button" className="ghost" onClick={onCancel}>
            Cancel
          </button>
          {onSchedule && (
            <button
              type="button"
              className="secondary"
              onClick={handleSchedule}
              disabled={scheduleSubmitting || taskSubmitting}
            >
              {scheduleSubmitting
                ? "Saving..."
                : scheduleOpen
                  ? "Save schedule"
                  : "Schedule"}
            </button>
          )}
          {onSubmit && (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={scheduleSubmitting || taskSubmitting}
            >
              Send now
            </button>
          )}
        </div>
      </section>
    </div>
  );

  return createPortal(content, document.body);
};

export default ToolEditorModal;
