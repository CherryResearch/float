const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const resolveAnchoredPopoverPosition = ({
  anchorRect,
  popoverRect,
  viewport,
  margin = 12,
  gap = 10,
  maxWidth = 460,
}) => {
  const viewportLeft = Number(viewport?.left) || 0;
  const viewportTop = Number(viewport?.top) || 0;
  const viewportWidth = Math.max(0, Number(viewport?.width) || 0);
  const viewportHeight = Math.max(0, Number(viewport?.height) || 0);
  const availableWidth = Math.max(0, viewportWidth - margin * 2);
  const width = Math.min(
    Math.max(0, Number(popoverRect?.width) || maxWidth),
    availableWidth,
    maxWidth,
  );
  const measuredHeight = Math.max(0, Number(popoverRect?.height) || 0);
  const maxHeight = Math.max(0, viewportHeight - margin * 2);
  const height = Math.min(measuredHeight || maxHeight, maxHeight);
  const minLeft = viewportLeft + margin;
  const maxLeft = Math.max(minLeft, viewportLeft + viewportWidth - width - margin);
  const left = clamp((Number(anchorRect?.right) || minLeft) - width, minLeft, maxLeft);
  const spaceAbove = (Number(anchorRect?.top) || viewportTop) - viewportTop - margin - gap;
  const spaceBelow =
    viewportTop + viewportHeight - (Number(anchorRect?.bottom) || viewportTop) - margin - gap;
  const placement = spaceAbove >= Math.min(height, spaceBelow) ? "above" : "below";
  const desiredTop =
    placement === "above"
      ? (Number(anchorRect?.top) || viewportTop) - height - gap
      : (Number(anchorRect?.bottom) || viewportTop) + gap;
  const minTop = viewportTop + margin;
  const maxTop = Math.max(minTop, viewportTop + viewportHeight - height - margin);

  return {
    top: Math.round(clamp(desiredTop, minTop, maxTop)),
    left: Math.round(left),
    maxHeight: Math.round(maxHeight),
    maxWidth: Math.round(availableWidth),
    placement,
  };
};
