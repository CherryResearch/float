const CLEARED_STATUSES = new Set(["acknowledged", "skipped"]);

const startOfMonth = (date) => new Date(date.getFullYear(), date.getMonth(), 1);

export const buildMonthGridDates = (monthDate) => {
  const firstDay = startOfMonth(monthDate);
  const gridStart = new Date(firstDay);
  gridStart.setDate(firstDay.getDate() - firstDay.getDay());
  const lastDay = new Date(firstDay.getFullYear(), firstDay.getMonth() + 1, 0);
  const gridEnd = new Date(lastDay);
  gridEnd.setDate(lastDay.getDate() + (6 - lastDay.getDay()));

  const arr = [];
  const cursor = new Date(gridStart);
  while (cursor <= gridEnd) {
    arr.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return arr;
};

export const isClearedCalendarStatus = (status) =>
  CLEARED_STATUSES.has(String(status || "").toLowerCase());

export const formatStatusLabel = (status) => {
  const normalized = String(status || "pending").toLowerCase();
  if (!normalized) return "pending";
  return normalized.replace(/_/g, " ");
};
