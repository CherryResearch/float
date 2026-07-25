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
});
