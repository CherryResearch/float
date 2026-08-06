const INPUT_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;
const pad = (item) => String(item).padStart(2, "0");

const inputParts = (value) => {
  const match = String(value || "").match(INPUT_PATTERN);
  if (!match) return null;
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
    hour: Number(match[4]),
    minute: Number(match[5]),
    second: Number(match[6] || 0),
  };
};

const formatterFor = (timeZone) =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });

const zonedParts = (date, timeZone) => {
  const values = {};
  formatterFor(timeZone)
    .formatToParts(date)
    .forEach((part) => {
      if (part.type !== "literal") values[part.type] = Number(part.value);
    });
  return {
    year: values.year,
    month: values.month,
    day: values.day,
    hour: values.hour,
    minute: values.minute,
    second: values.second,
  };
};

const partsAsUtc = (parts) =>
  Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second || 0,
  );

export const zonedInputToDate = (value, timeZone, { gapPolicy = "reject" } = {}) => {
  const target = inputParts(value);
  if (!target) return null;
  const fallback = new Date(value);
  if (!timeZone) return Number.isNaN(fallback.getTime()) ? null : fallback;
  try {
    const targetWallClock = partsAsUtc(target);
    let timestamp = targetWallClock;
    let laterGapCandidate = null;
    let laterGapDelta = Number.POSITIVE_INFINITY;
    // Intl exposes zone conversion but not its inverse. Iterating the observed
    // wall-clock delta converges in one or two steps, including across DST.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const observed = zonedParts(new Date(timestamp), timeZone);
      const correction = targetWallClock - partsAsUtc(observed);
      const observedDelta = partsAsUtc(observed) - targetWallClock;
      if (observedDelta >= 0 && observedDelta < laterGapDelta) {
        laterGapCandidate = new Date(timestamp);
        laterGapDelta = observedDelta;
      }
      timestamp += correction;
      if (correction === 0) break;
    }
    const result = new Date(timestamp);
    const observed = zonedParts(result, timeZone);
    if (partsAsUtc(observed) !== targetWallClock) {
      return gapPolicy === "later" ? laterGapCandidate : null;
    }
    return result;
  } catch {
    return Number.isNaN(fallback.getTime()) ? null : fallback;
  }
};

export const dateToZonedInput = (value, timeZone, { seconds = false } = {}) => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  let parts;
  try {
    parts = timeZone
      ? zonedParts(date, timeZone)
      : {
          year: date.getFullYear(),
          month: date.getMonth() + 1,
          day: date.getDate(),
          hour: date.getHours(),
          minute: date.getMinutes(),
          second: date.getSeconds(),
        };
  } catch {
    parts = {
      year: date.getFullYear(),
      month: date.getMonth() + 1,
      day: date.getDate(),
      hour: date.getHours(),
      minute: date.getMinutes(),
      second: date.getSeconds(),
    };
  }
  const base = `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
  return seconds ? `${base}:${pad(parts.second || 0)}` : base;
};

export const civilDateKey = (value) => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

export const dateKeyInTimeZone = (value, timeZone) =>
  dateToZonedInput(value, timeZone).slice(0, 10);

export const hourInTimeZone = (value, timeZone) => {
  const input = dateToZonedInput(value, timeZone);
  return input ? Number(input.slice(11, 13)) : Number.NaN;
};

export const formatTimeInTimeZone = (value, timeZone) => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return date.toLocaleTimeString([], {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
};

export const addZonedWallIntervalInput = (source, frequency, amount) => {
  const parts = inputParts(source);
  if (!parts) return "";
  const wallClock = new Date(partsAsUtc(parts));
  const numericAmount = Number(amount);
  const step = Number.isFinite(numericAmount) ? numericAmount : 1;
  if (frequency === "minutes") wallClock.setUTCMinutes(wallClock.getUTCMinutes() + step);
  if (frequency === "hours") wallClock.setUTCHours(wallClock.getUTCHours() + step);
  if (frequency === "days") wallClock.setUTCDate(wallClock.getUTCDate() + step);
  if (frequency === "weeks") wallClock.setUTCDate(wallClock.getUTCDate() + step * 7);
  return `${wallClock.getUTCFullYear()}-${pad(
    wallClock.getUTCMonth() + 1,
  )}-${pad(wallClock.getUTCDate())}T${pad(wallClock.getUTCHours())}:${pad(
    wallClock.getUTCMinutes(),
  )}:${pad(wallClock.getUTCSeconds())}`;
};

export const addZonedCalendarInterval = (value, frequency, amount, timeZone) => {
  const source = dateToZonedInput(value, timeZone, { seconds: true });
  const nextInput = addZonedWallIntervalInput(source, frequency, amount);
  if (!nextInput) return null;
  return zonedInputToDate(nextInput, timeZone, { gapPolicy: "later" });
};

export const addZonedCalendarMonths = (value, amount, timeZone) => {
  const source = dateToZonedInput(value, timeZone, { seconds: true });
  const parts = inputParts(source);
  if (!parts) return null;
  const wallClock = new Date(partsAsUtc(parts));
  wallClock.setUTCMonth(wallClock.getUTCMonth() + (Number(amount) || 0));
  const nextInput = `${wallClock.getUTCFullYear()}-${pad(
    wallClock.getUTCMonth() + 1,
  )}-${pad(wallClock.getUTCDate())}T${pad(wallClock.getUTCHours())}:${pad(
    wallClock.getUTCMinutes(),
  )}:${pad(wallClock.getUTCSeconds())}`;
  return zonedInputToDate(nextInput, timeZone, { gapPolicy: "later" });
};

export const defaultSeriesEndInput = (startValue, frequency, timeZone) => {
  const start = zonedInputToDate(startValue, timeZone) || new Date();
  const durationDays = ["days", "weeks"].includes(frequency) ? 30 : 1;
  const end = addZonedCalendarInterval(start, "days", durationDays, timeZone);
  return dateToZonedInput(end || start, timeZone);
};
