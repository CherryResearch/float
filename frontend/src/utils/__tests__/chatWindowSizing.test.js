import { describe, expect, it } from "vitest";

import {
  canResizeChatWindow,
  clampChatWindowWidth,
  getChatWindowWidthBounds,
  parseStoredChatWindowWidth,
} from "../chatWindowSizing";

describe("chatWindowSizing", () => {
  it("derives sane resize bounds from the viewport width", () => {
    expect(getChatWindowWidthBounds(1920)).toEqual({
      minWidth: 760,
      maxWidth: 1480,
    });
    expect(getChatWindowWidthBounds(840)).toEqual({
      minWidth: 760,
      maxWidth: 808,
    });
  });

  it("clamps stored and interactive widths into those bounds", () => {
    expect(clampChatWindowWidth(400, 1280)).toBe(760);
    expect(clampChatWindowWidth(1100, 1280)).toBe(1100);
    expect(clampChatWindowWidth(5000, 1280)).toBe(1248);
    expect(parseStoredChatWindowWidth("1012.8", 1280)).toBe(1013);
    expect(parseStoredChatWindowWidth("not-a-number", 1280)).toBeNull();
  });

  it("disables the desktop resizer when the viewport is too narrow", () => {
    expect(canResizeChatWindow(780)).toBe(false);
    expect(canResizeChatWindow(1024)).toBe(true);
  });
});
