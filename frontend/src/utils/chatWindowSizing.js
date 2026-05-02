export const CHAT_WINDOW_STORAGE_KEY = "chatWindowWidth";
export const CHAT_WINDOW_MIN_WIDTH = 760;
export const CHAT_WINDOW_MAX_WIDTH = 1480;
export const CHAT_WINDOW_VIEWPORT_GUTTER = 32;
export const CHAT_WINDOW_KEYBOARD_STEP = 24;
export const CHAT_WINDOW_KEYBOARD_STEP_FAST = 48;

const normalizeViewportWidth = (viewportWidth) => {
  const parsed = Number(viewportWidth);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  return CHAT_WINDOW_MAX_WIDTH;
};

export const getChatWindowWidthBounds = (viewportWidth) => {
  const safeViewportWidth = normalizeViewportWidth(viewportWidth);
  const maxWidth = Math.min(
    CHAT_WINDOW_MAX_WIDTH,
    Math.max(320, Math.floor(safeViewportWidth - CHAT_WINDOW_VIEWPORT_GUTTER)),
  );
  const minWidth = Math.min(
    maxWidth,
    Math.max(560, Math.min(CHAT_WINDOW_MIN_WIDTH, maxWidth)),
  );
  return {
    minWidth,
    maxWidth,
  };
};

export const clampChatWindowWidth = (width, viewportWidth) => {
  const { minWidth, maxWidth } = getChatWindowWidthBounds(viewportWidth);
  const parsed = Number(width);
  if (!Number.isFinite(parsed)) return minWidth;
  return Math.min(maxWidth, Math.max(minWidth, Math.round(parsed)));
};

export const parseStoredChatWindowWidth = (rawValue, viewportWidth) => {
  const parsed = Number.parseFloat(String(rawValue || ""));
  if (!Number.isFinite(parsed)) return null;
  return clampChatWindowWidth(parsed, viewportWidth);
};

export const canResizeChatWindow = (viewportWidth) => {
  const { minWidth, maxWidth } = getChatWindowWidthBounds(viewportWidth);
  return maxWidth - minWidth >= 48;
};
