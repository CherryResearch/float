import React, { cloneElement, forwardRef, useId, useState } from "react";
import Tooltip from "@mui/material/Tooltip";
import "../../styles/UiControls.css";

/** A labelled native field with the same spacing and help-text association everywhere. */
export function ControlField({ label, children, hint, className = "", id }) {
  const generatedId = useId();
  const inputId = id || children.props.id || generatedId;
  const hintId = `${inputId}-hint`;
  const describedBy = [children.props["aria-describedby"], hint && hintId]
    .filter(Boolean)
    .join(" ");
  const isCheck = ["checkbox", "radio"].includes(children.props.type);
  const control = cloneElement(children, {
    id: inputId,
    "aria-describedby": describedBy || undefined,
  });

  return (
    <div className={`ui-field${isCheck ? " ui-field--check" : ""} ${className}`}>
      {isCheck ? control : null}
      <label className="ui-field__label" htmlFor={inputId}>{label}</label>
      {isCheck ? null : control}
      {hint ? <span className="ui-field__hint" id={hintId}>{hint}</span> : null}
    </div>
  );
}

/** Native disclosure keeps keyboard behavior and hidden controls consistent. */
export function DisclosurePanel({
  title,
  summary,
  children,
  className = "",
  defaultOpen = false,
  open,
  onToggle,
  id,
}) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const isOpen = open ?? expanded;
  return (
    <details
      id={id}
      className={`ui-disclosure ${className}`}
      open={isOpen}
      onToggle={(event) => {
        const next = event.currentTarget.open;
        if (open === undefined) setExpanded(next);
        if (next !== isOpen) onToggle?.(next);
      }}
    >
      <summary className="ui-disclosure__summary">
        <span className="ui-disclosure__title">{title}</span>
        {summary ? <span className="ui-disclosure__description">{summary}</span> : null}
      </summary>
      <div className="ui-disclosure__body">{children}</div>
    </details>
  );
}

/** One hit area, accessible name, and disabled-tooltip behavior for small actions. */
export const IconAction = forwardRef(function IconAction(
  { label, tooltip = label, children, className = "", disabled, ...buttonProps },
  ref,
) {
  const button = (
    <button
      type="button"
      {...buttonProps}
      ref={ref}
      className={`ui-icon-action ${className}`}
      aria-label={label}
      disabled={disabled}
    >
      {children}
    </button>
  );
  return (
    <span className="ui-icon-action-wrap">
      <Tooltip title={tooltip} describeChild>
        {disabled ? <span className="ui-icon-action-wrap">{button}</span> : button}
      </Tooltip>
    </span>
  );
});
