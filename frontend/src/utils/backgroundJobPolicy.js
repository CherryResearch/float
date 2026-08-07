import {
  addZonedWallIntervalInput,
  dateToZonedInput,
  defaultSeriesEndInput,
  zonedInputToDate,
} from "./zonedDateTime";

export const DEFAULT_BACKGROUND_JOB_POLICY = Object.freeze({
  schema_version: 1,
  patience: {
    stop_condition: "one_pass",
    max_attempts: 1,
    max_provider_retries: 2,
    max_runtime_seconds: 900,
    satisfied_threshold: 0.8,
  },
  execution: {
    reasoning_effort: "inherit",
    model: "inherit",
    workflow: "inherit",
    allow_subagents: true,
    sandbox_processes: true,
    permissions: [],
  },
  ownership: {
    owner_kind: "calendar_event",
  },
});

const asObject = (value) =>
  value && typeof value === "object" && !Array.isArray(value) ? value : {};

export const normalizeBackgroundJobPolicy = (value = {}) => {
  const raw = asObject(value);
  const patience = asObject(raw.patience);
  const execution = asObject(raw.execution);
  const ownership = asObject(raw.ownership);
  return {
    ...raw,
    schema_version: Number(raw.schema_version) || 1,
    patience: {
      ...DEFAULT_BACKGROUND_JOB_POLICY.patience,
      ...patience,
    },
    execution: {
      ...DEFAULT_BACKGROUND_JOB_POLICY.execution,
      ...execution,
      permissions: Array.isArray(execution.permissions)
        ? execution.permissions.filter(Boolean).map(String)
        : [],
    },
    ownership: {
      ...DEFAULT_BACKGROUND_JOB_POLICY.ownership,
      ...ownership,
    },
  };
};

const parseUntil = (value, timeZone) => {
  const match = String(value || "").match(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/,
  );
  if (!match) return null;
  const [, year, month, day, hour, minute, second, utc] = match;
  const parts = [year, month, day, hour, minute, second].map(Number);
  const date = utc
    ? new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4], parts[5]))
    : zonedInputToDate(
        `${year}-${month}-${day}T${hour}:${minute}:${second}`,
        timeZone,
      );
  if (!date) return null;
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatUntil = (value, timeZone) => {
  const date = value instanceof Date ? value : zonedInputToDate(value, timeZone);
  if (!date) return "";
  if (Number.isNaN(date.getTime())) return "";
  return date
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}Z$/, "Z");
};

export const parseRecurrenceRule = (rrule = "", timeZone = "") => {
  const raw = String(rrule || "").trim().replace(/^RRULE:/i, "");
  if (!raw) {
    return {
      raw: "",
      frequency: "once",
      interval: 1,
      endMode: "never",
      untilInput: "",
      count: 1,
      isCustom: false,
    };
  }
  const values = {};
  let isCustom = false;
  raw.split(";").forEach((part) => {
    const [key, ...rest] = part.split("=");
    if (!key || !rest.length) return;
    const normalizedKey = key.trim().toUpperCase();
    values[normalizedKey] = rest.join("=").trim();
    if (!["FREQ", "INTERVAL", "UNTIL", "COUNT"].includes(normalizedKey)) {
      isCustom = true;
    }
  });
  const frequencyMap = {
    MINUTELY: "minutes",
    HOURLY: "hours",
    DAILY: "days",
    WEEKLY: "weeks",
  };
  const parsedFrequency =
    frequencyMap[String(values.FREQ || "").toUpperCase()] || "custom";
  const untilDate = parseUntil(values.UNTIL, timeZone);
  const parsedCount = Number.parseInt(values.COUNT || "", 10);
  const custom = isCustom || parsedFrequency === "custom";
  return {
    raw,
    frequency: custom ? "custom" : parsedFrequency,
    interval: Math.max(1, Number.parseInt(values.INTERVAL || "1", 10) || 1),
    endMode: untilDate ? "until" : Number.isFinite(parsedCount) ? "count" : "never",
    untilInput: untilDate ? dateToZonedInput(untilDate, timeZone) : "",
    count: Number.isFinite(parsedCount) ? Math.max(1, parsedCount) : 1,
    isCustom: custom,
  };
};

export const buildRecurrenceRule = ({
  frequency,
  interval = 1,
  endMode = "never",
  untilInput = "",
  count = 1,
  raw = "",
  timeZone = "",
}) => {
  if (frequency === "once") return "";
  if (frequency === "custom") return String(raw || "").trim().replace(/^RRULE:/i, "");
  const frequencyMap = {
    minutes: "MINUTELY",
    hours: "HOURLY",
    days: "DAILY",
    weeks: "WEEKLY",
  };
  const freq = frequencyMap[frequency];
  if (!freq) return "";
  const parts = [`FREQ=${freq}`, `INTERVAL=${Math.max(1, Number(interval) || 1)}`];
  if (endMode === "until" && untilInput) {
    const until = formatUntil(untilInput, timeZone);
    if (until) parts.push(`UNTIL=${until}`);
  } else if (endMode === "count") {
    parts.push(`COUNT=${Math.max(1, Number(count) || 1)}`);
  }
  return parts.join(";");
};

export const recurrenceSummary = (rrule = "", timeZone = "") => {
  const parsed = parseRecurrenceRule(rrule, timeZone);
  if (parsed.frequency === "once") return "Once";
  if (parsed.frequency === "custom" || parsed.isCustom) return `Custom · ${parsed.raw}`;
  const singular = {
    minutes: "minute",
    hours: "hour",
    days: "day",
    weeks: "week",
  }[parsed.frequency];
  const cadence =
    parsed.interval === 1 ? `Every ${singular}` : `Every ${parsed.interval} ${singular}s`;
  if (parsed.endMode === "count") return `${cadence} · ${parsed.count} runs`;
  if (parsed.endMode === "until" && parsed.untilInput) {
    const until = zonedInputToDate(parsed.untilInput, timeZone);
    const label = until
      ? until.toLocaleString([], timeZone ? { timeZone } : undefined)
      : parsed.untilInput;
    return `${cadence} · until ${label}`;
  }
  return cadence;
};

export const previewOccurrences = (
  startValue,
  recurrence,
  maxItems = 3,
  timeZone = "",
) => {
  const start =
    startValue instanceof Date
      ? new Date(startValue)
      : zonedInputToDate(startValue, timeZone) || new Date(startValue);
  if (Number.isNaN(start.getTime())) return [];
  const parsed = parseRecurrenceRule(recurrence, timeZone);
  if (parsed.frequency === "once") return [start];
  if (parsed.frequency === "custom" || parsed.isCustom) return [];
  const until = parsed.untilInput
    ? zonedInputToDate(parsed.untilInput, timeZone)
    : null;
  const total = parsed.endMode === "count" ? parsed.count : maxItems;
  const out = [];
  let wallCursor = dateToZonedInput(start, timeZone, { seconds: true });
  for (let index = 0; index < Math.min(total, maxItems); index += 1) {
    const cursor = zonedInputToDate(wallCursor, timeZone, { gapPolicy: "later" });
    if (!cursor || Number.isNaN(cursor.getTime())) break;
    if (until && cursor > until) break;
    out.push(new Date(cursor));
    const nextWallCursor = addZonedWallIntervalInput(
      wallCursor,
      parsed.frequency,
      parsed.interval,
    );
    if (!nextWallCursor) break;
    wallCursor = nextWallCursor;
  }
  return out;
};

export { defaultSeriesEndInput };
