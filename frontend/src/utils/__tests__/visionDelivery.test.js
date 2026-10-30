import { describe, expect, it } from "vitest";
import { getMessageVisionNotice } from "../visionDelivery";

const messageWith = (attachments, extra = {}) => ({
  metadata: {
    vision: {
      native_image_input: false,
      fallback_used: true,
      fallback_attachments: attachments,
      ...extra,
    },
  },
});

describe("image delivery notice scope", () => {
  it("does not put an earlier image warning on a text-only continuation", () => {
    expect(getMessageVisionNotice(messageWith([
      { name: "earlier.png", scope: "history", source_message_id: "earlier-user", placeholder: false },
    ]))).toBeNull();
  });

  it("keeps the current caption notice separate from an unavailable historical image", () => {
    const notice = getMessageVisionNotice(messageWith([
      { name: "earlier.png", scope: "history", placeholder: true },
      { name: "current.png", scope: "current", placeholder: false },
    ]));
    expect(notice.title).toBe("Image described locally");
  });

  it("uses the initiating turn to suppress legacy warnings on no-image turns", () => {
    const legacy = messageWith([{ name: "camera.png", capture_id: "camera-1", placeholder: false }]);
    expect(getMessageVisionNotice(legacy, { currentTurnAttachments: [] })).toBeNull();
    expect(getMessageVisionNotice(legacy, {
      currentTurnAttachments: [{ type: "image/png", capture_id: "camera-1" }],
    }).title).toBe("Image described locally");
    expect(getMessageVisionNotice(legacy, {
      currentTurnAttachments: [{ type: "image/png", capture_id: "different-camera" }],
    })).toBeNull();
  });

  it("describes mixed native and fallback delivery without claiming all images were unseen", () => {
    const notice = getMessageVisionNotice(messageWith([
      { name: "current.png", scope: "current", placeholder: true },
    ], { native_image_input: true }));
    expect(notice.title).toBe("Some images not seen");
    expect(notice.message).toContain("model received the other images");
  });
});
