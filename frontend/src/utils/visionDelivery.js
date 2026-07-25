export const getMessageVisionNotice = (msg) => {
  if (!msg || typeof msg !== "object") return null;
  const metadata = msg.metadata && typeof msg.metadata === "object" ? msg.metadata : {};
  const vision = metadata.vision && typeof metadata.vision === "object" ? metadata.vision : null;
  if (!vision || vision.native_image_input !== false || vision.fallback_used !== true) {
    return null;
  }

  const fallbackAttachments = Array.isArray(vision.fallback_attachments)
    ? vision.fallback_attachments
    : [];
  const placeholderCount = fallbackAttachments.filter(
    (attachment) => attachment && attachment.placeholder === true,
  ).length;

  if (placeholderCount > 0) {
    return {
      tone: "unavailable",
      title: "Image not seen",
      message:
        "The selected model did not receive visual content. Float could not generate a usable local image description, so this reply may rely only on your text.",
    };
  }

  return {
    tone: "fallback",
    title: "Image described locally",
    message:
      "The selected model did not receive the image directly. Float supplied a locally generated text description instead.",
  };
};
