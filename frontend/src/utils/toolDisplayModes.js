export const TOOL_DISPLAY_MODES = ["console", "inline", "both", "auto"];

export const normalizeToolDisplayMode = (value) => {
  const raw = value == null ? "" : String(value).trim().toLowerCase();
  return TOOL_DISPLAY_MODES.includes(raw) ? raw : "console";
};

export const normalizeToolLinkBehavior = (value) => {
  const raw = value == null ? "" : String(value).trim().toLowerCase();
  return raw === "inline" ? "inline" : "console";
};

export const toolDisplaySupportsInline = (mode) =>
  normalizeToolDisplayMode(mode) !== "console";

export const toolDisplayShowsConsole = (mode) =>
  normalizeToolDisplayMode(mode) !== "inline";
