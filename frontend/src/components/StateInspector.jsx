import React, { useEffect, useMemo, useRef, useState } from "react";
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
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const normalizedRows = useMemo(() => normalizeStateInspectorRows(rows), [rows]);
  const tooltip = useMemo(
    () => buildStateInspectorTitle({ title, summary, rows: normalizedRows }),
    [title, summary, normalizedRows],
  );

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) {
        setOpen(false);
      }
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

  if (!summary && normalizedRows.length === 0) return null;

  return (
    <span
      ref={rootRef}
      className={`state-inspector${className ? ` ${className}` : ""}`}
      data-open={open ? "true" : "false"}
      onClick={(event) => event.stopPropagation()}
    >
      <button
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
      {open && (
        <span className="state-inspector-panel" role="dialog" aria-label={title}>
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
        </span>
      )}
    </span>
  );
};

export default StateInspector;
