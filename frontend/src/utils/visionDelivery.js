const sameAttachment = (left, right) => {
  for (const key of ["content_hash", "capture_id", "url"]) {
    if (left?.[key] && right?.[key]) return left[key] === right[key];
  }
  return Boolean(left?.name && left.name === right?.name);
};

export const getMessageVisionNotice = (msg, { currentTurnAttachments } = {}) => {
  if (!msg || typeof msg !== "object") return null;
  const metadata = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
  const vision = metadata.vision && typeof metadata.vision === "object" ? metadata.vision : null;
  if (!vision || vision.fallback_used !== true) {
    return null;
  }

  let fallbackAttachments = Array.isArray(vision.fallback_attachments)
    ? vision.fallback_attachments
    : [];
  const hasScopedEvidence = fallbackAttachments.some((attachment) => attachment?.scope);
  if (hasScopedEvidence) {
    fallbackAttachments = fallbackAttachments.filter((attachment) => attachment?.scope === "current");
    if (!fallbackAttachments.length) return null;
  } else if (Array.isArray(currentTurnAttachments)) {
    // Older transcripts lack delivery scope. Match the initiating user turn so
    // an earlier photo does not become a warning on an unrelated tool approval.
    const currentImages = currentTurnAttachments.filter(
      (attachment) =>
        String(attachment?.type || attachment?.content_type || "").startsWith("image/") ||
        /\.(png|jpe?g|gif|svg|webp)$/i.test(attachment?.name || ""),
    );
    if (!currentImages.length) return null;
    if (fallbackAttachments.length) {
      fallbackAttachments = fallbackAttachments.filter((fallback) =>
        currentImages.some((current) => sameAttachment(fallback, current)),
      );
      if (!fallbackAttachments.length) return null;
    }
  }
  if (!hasScopedEvidence && vision.native_image_input !== false) return null;
  const placeholderCount = fallbackAttachments.filter(
    (attachment) => attachment && attachment.placeholder === true,
  ).length;

  if (placeholderCount > 0) {
    return {
      tone: "unavailable",
      title: vision.native_image_input ? "Some images not seen" : "Image not seen",
      message: vision.native_image_input
        ? "Some attached images could not be delivered or described. The model received the other images; check which images this reply refers to."
        : "The selected model did not receive visual content. Float could not generate a usable local image description, so this reply may rely only on your text.",
    };
  }

  return {
    tone: "fallback",
    title: vision.native_image_input ? "Some images described locally" : "Image described locally",
    message: vision.native_image_input
      ? "The model received some attached images directly and locally generated text descriptions for the others."
      : "The selected model did not receive the image directly. Float supplied a locally generated text description instead.",
  };
};
