import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "../styles/StateInspector.css";

const stringifyValue = (value) => {
  if (value === null || typeof value === "undefined") return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map(stringifyValue).filter(Boolean).join(", ");
  }
  return "";
};

export const normalizeStateInspectorRows = (rows = []) =>
  (Array.isArray(rows) ? rows : [])
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const label = stringifyValue(row.label);
      const value = stringifyValue(row.value);
      if (!label || !value) return null;
      return { label, value };
    })
    .filter(Boolean);

export const buildStateInspectorTitle = ({ title, summary, rows } = {}) => {
  const normalizedRows = normalizeStateInspectorRows(rows);
  return [
    stringifyValue(title) || "Why am I seeing this?",
    stringifyValue(summary),
    ...normalizedRows.map((row) => `${row.label}: ${row.value}`),
  ]
    .filter(Boolean)
    .join(" | ");
};

const StateInspector = ({
  title = "Why am I seeing this?",
  summary = "",
  rows = [],
  label = "?",
  className = "",
  ariaLabel,
  placement = "bottom",
}) => {
  const [open, setOpen] = useState(false);
  const [panelPosition, setPanelPosition] = useState(null);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const panelRef = useRef(null);
  const normalizedRows = useMemo(() => normalizeStateInspectorRows(rows), [rows]);
  const tooltip = useMemo(
    () => buildStateInspectorTitle({ title, summary, rows: normalizedRows }),
    [title, summary, normalizedRows],
  );

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (rootRef.current?.contains(event.target) || panelRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || typeof window === "undefined") return undefined;
    const updatePosition = () => {
      const button = buttonRef.current;
      const panel = panelRef.current;
      if (!button || !panel) return;
      const buttonRect = button.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
      const edge = 8;
      const gap = 6;
      const panelWidth = Math.min(panelRect.width || 320, Math.max(0, viewportWidth - edge * 2));
      const panelHeight = panelRect.height || 0;
      const maxLeft = Math.max(edge, viewportWidth - panelWidth - edge);
      const left = Math.min(maxLeft, Math.max(edge, buttonRect.right - panelWidth));
      const belowTop = buttonRect.bottom + gap;
      const aboveTop = buttonRect.top - panelHeight - gap;
      const preferTop = placement === "top";
      let top = preferTop ? aboveTop : belowTop;
      if (preferTop && top < edge) top = belowTop;
      if (!preferTop && top + panelHeight > viewportHeight - edge && aboveTop >= edge) {
        top = aboveTop;
      }
      top = Math.max(edge, Math.min(top, Math.max(edge, viewportHeight - panelHeight - edge)));
      setPanelPosition({ left, top });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, placement, normalizedRows, summary, title]);

  if (!summary && normalizedRows.length === 0) return null;

  return (
    <span
      ref={rootRef}
      className={`state-inspector${className ? ` ${className}` : ""}`}
      data-open={open ? "true" : "false"}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        ref={buttonRef}
        type="button"
        className="state-inspector-button"
        aria-label={ariaLabel || title || "Explain state"}
        aria-expanded={open}
        title={tooltip}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {label}
      </button>
      {open && typeof document !== "undefined" && createPortal(
        <span
          ref={panelRef}
          className="state-inspector-panel"
          role="dialog"
          aria-label={title}
          data-placement={placement === "top" ? "top" : "bottom"}
          style={panelPosition ? panelPosition : { visibility: "hidden" }}
          onClick={(event) => event.stopPropagation()}
        >
          <span className="state-inspector-title">{title}</span>
          {summary ? <span className="state-inspector-summary">{summary}</span> : null}
          {normalizedRows.length ? (
            <dl className="state-inspector-grid">
              {normalizedRows.map((row) => (
                <React.Fragment key={`${row.label}:${row.value}`}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </React.Fragment>
              ))}
            </dl>
          ) : null}
        </span>,
        document.body,
      )}
    </span>
  );
};

export default StateInspector;
