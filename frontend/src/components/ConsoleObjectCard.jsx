import React from "react";

const INTERACTIVE_SELECTOR =
  'button, a, input, textarea, select, label, summary, [role="button"], [data-no-card-toggle="true"]';

const eventStartedInInteractive = (event) => {
  const target = event?.target;
  return target instanceof Element && Boolean(target.closest(INTERACTIVE_SELECTOR));
};

const ConsoleObjectCard = ({
  title,
  subtitle = "",
  preview = "",
  ariaLabel,
  className = "",
  tone = "",
  collapsed = false,
  onToggleCollapsed = null,
  onHide = null,
  extraActions = null,
  status = null,
  children = null,
  collapsedContent = null,
  controlButtonClassName = "agent-card-control-btn",
  symbolButtonClassName = "agent-card-control-symbol",
  expandLabel,
  collapseLabel,
  hideLabel,
  toggleTitle,
  hideTitle,
  toggleOnCardClick = true,
}) => {
  const canToggle = typeof onToggleCollapsed === "function";
  const canHide = typeof onHide === "function";
  const resolvedExpandLabel = expandLabel || `Expand ${title}`;
  const resolvedCollapseLabel = collapseLabel || `Collapse ${title}`;
  const resolvedHideLabel = hideLabel || `Hide ${title}`;
  const resolvedToggleLabel = collapsed ? resolvedExpandLabel : resolvedCollapseLabel;
  const resolvedToggleTitle = toggleTitle || resolvedToggleLabel;
  const resolvedHideTitle = hideTitle || resolvedHideLabel;
  const hasPreview = Boolean(String(preview || "").trim());
  const sectionClass = [
    "agent-console-object-card",
    className,
    collapsed ? "is-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const handleCardClick = (event) => {
    if (!canToggle || !toggleOnCardClick || eventStartedInInteractive(event)) return;
    onToggleCollapsed();
  };

  const handleCardKeyDown = (event) => {
    if (!canToggle || !toggleOnCardClick || eventStartedInInteractive(event)) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onToggleCollapsed();
  };

  return (
    <section
      className={sectionClass}
      aria-label={ariaLabel || title}
      data-collapsed={collapsed ? "true" : "false"}
      data-tone={tone || undefined}
      data-click-toggle={canToggle && toggleOnCardClick ? "true" : undefined}
      onClick={handleCardClick}
      onKeyDown={handleCardKeyDown}
      tabIndex={canToggle && toggleOnCardClick ? 0 : undefined}
    >
      <header className="agent-console-object-header">
        <div className="agent-console-object-title">
          <h3>{title}</h3>
          {subtitle ? (
            <span className="agent-console-object-subtitle" title={subtitle}>
              {subtitle}
            </span>
          ) : null}
        </div>
        <div className="agent-console-object-actions" data-no-card-toggle="true">
          {extraActions}
          {status}
          {canToggle ? (
            <button
              type="button"
              className={`${controlButtonClassName} ${symbolButtonClassName}`}
              aria-expanded={!collapsed}
              aria-label={resolvedToggleLabel}
              title={resolvedToggleTitle}
              onClick={(event) => {
                event.stopPropagation();
                onToggleCollapsed();
              }}
            >
              {collapsed ? "+" : "-"}
            </button>
          ) : null}
          {canHide ? (
            <button
              type="button"
              className={`${controlButtonClassName} ${symbolButtonClassName} danger`}
              aria-label={resolvedHideLabel}
              title={resolvedHideTitle}
              onClick={(event) => {
                event.stopPropagation();
                onHide();
              }}
            >
              X
            </button>
          ) : null}
        </div>
      </header>
      {collapsed ? collapsedContent : <div className="agent-console-object-body">{children}</div>}
      {hasPreview ? (
        <div className="agent-console-object-hover-preview" aria-hidden="true">
          {preview}
        </div>
      ) : null}
    </section>
  );
};

export default ConsoleObjectCard;
