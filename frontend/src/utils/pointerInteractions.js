const SYNTHETIC_CLICK_SUPPRESSION_MS = 750;

export const supportsHoverInteractions = () => {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
};

export const handleUnifiedPress = (
  event,
  onPress,
  { stopPropagation = true, preventDefaultOnPointerDown = true } = {},
) => {
  const target = event?.currentTarget;
  if (event?.type === "pointerdown") {
    if (typeof event.button === "number" && event.button !== 0) {
      return;
    }
    if (preventDefaultOnPointerDown && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (stopPropagation && typeof event.stopPropagation === "function") {
      event.stopPropagation();
    }
    if (target && typeof target === "object") {
      target.__lastUnifiedPressAt = Date.now();
    }
    onPress?.(event);
    return;
  }

  if (event?.type === "click" && target?.__lastUnifiedPressAt) {
    const elapsed = Date.now() - target.__lastUnifiedPressAt;
    target.__lastUnifiedPressAt = 0;
    if (elapsed >= 0 && elapsed < SYNTHETIC_CLICK_SUPPRESSION_MS) {
      return;
    }
  }

  if (stopPropagation && typeof event?.stopPropagation === "function") {
    event.stopPropagation();
  }
  onPress?.(event);
};
