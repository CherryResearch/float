import { describe, expect, it } from "vitest";

import { resolveAnchoredPopoverPosition } from "../popoverPosition";

describe("resolveAnchoredPopoverPosition", () => {
  it("places a composer popover above its anchor when space is available", () => {
    expect(
      resolveAnchoredPopoverPosition({
        anchorRect: { top: 740, bottom: 770, right: 1200 },
        popoverRect: { width: 460, height: 300 },
        viewport: { left: 0, top: 0, width: 1440, height: 900 },
      }),
    ).toMatchObject({ top: 430, left: 740, placement: "above" });
  });

  it("clamps a growing panel inside a short viewport", () => {
    const position = resolveAnchoredPopoverPosition({
      anchorRect: { top: 510, bottom: 540, right: 1012 },
      popoverRect: { width: 460, height: 720 },
      viewport: { left: 0, top: 0, width: 1024, height: 600 },
    });

    expect(position).toMatchObject({
      top: 12,
      left: 552,
      maxHeight: 576,
      placement: "above",
    });
    expect(position.top + position.maxHeight).toBeLessThanOrEqual(588);
  });

  it("respects visual viewport offsets and narrow-screen margins", () => {
    expect(
      resolveAnchoredPopoverPosition({
        anchorRect: { top: 610, bottom: 640, right: 384 },
        popoverRect: { width: 460, height: 500 },
        viewport: { left: 4, top: 20, width: 390, height: 700 },
        margin: 8,
      }),
    ).toMatchObject({ left: 12, maxWidth: 374, maxHeight: 684 });
  });

  it("aligns a left-side attachment menu with the start of its trigger", () => {
    expect(
      resolveAnchoredPopoverPosition({
        anchorRect: { top: 740, bottom: 780, left: 300, right: 340 },
        popoverRect: { width: 216, height: 144 },
        viewport: { left: 0, top: 0, width: 1440, height: 900 },
        align: "start",
      }),
    ).toMatchObject({ top: 586, left: 300, placement: "above" });
  });

  it.each([
    { anchorLeft: 95, expectedLeft: 112 },
    { anchorLeft: 450, expectedLeft: 262 },
  ])("clamps a start-aligned menu at anchor $anchorLeft within the visible viewport", ({
    anchorLeft,
    expectedLeft,
  }) => {
    const position = resolveAnchoredPopoverPosition({
      anchorRect: { top: 610, bottom: 650, left: anchorLeft, right: anchorLeft + 40 },
      popoverRect: { width: 216, height: 144 },
      viewport: { left: 100, top: 20, width: 390, height: 700 },
      align: "start",
    });

    expect(position.left).toBe(expectedLeft);
    expect(position.left + 216).toBeLessThanOrEqual(478);
    expect(position.left).toBeGreaterThanOrEqual(112);
  });
});
