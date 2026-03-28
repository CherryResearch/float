import React, { useEffect, useMemo, useState } from "react";

import {
  formatDelimiterLabel,
  parseCsvPreview,
  shouldUseFirstRowAsHeader,
} from "../utils/csvPreview";

const buildFallbackHeaders = (count) =>
  Array.from({ length: count }, (_, index) => `column ${index + 1}`);

const CsvTablePreview = ({ text = "" }) => {
  const preview = useMemo(() => parseCsvPreview(text), [text]);
  const [useFirstRowAsHeader, setUseFirstRowAsHeader] = useState(() =>
    shouldUseFirstRowAsHeader(preview.rows),
  );

  useEffect(() => {
    setUseFirstRowAsHeader(shouldUseFirstRowAsHeader(preview.rows));
  }, [preview.rows]);

  const previewColumns = useMemo(
    () => Math.max(preview.totalColumns, ...preview.rows.map((row) => row.length), 0),
    [preview.rows, preview.totalColumns],
  );

  const fallbackHeaders = useMemo(
    () => buildFallbackHeaders(previewColumns),
    [previewColumns],
  );

  const headerCells = useMemo(() => {
    if (!previewColumns) return [];
    if (!useFirstRowAsHeader || preview.rows.length === 0) {
      return fallbackHeaders;
    }
    return fallbackHeaders.map((label, index) => preview.rows[0]?.[index] || label);
  }, [fallbackHeaders, preview.rows, previewColumns, useFirstRowAsHeader]);

  const bodyRows = useMemo(() => {
    if (!preview.rows.length) return [];
    return useFirstRowAsHeader ? preview.rows.slice(1) : preview.rows;
  }, [preview.rows, useFirstRowAsHeader]);

  const rowOffset = useFirstRowAsHeader ? 2 : 1;
  const summaryBits = [
    `${formatDelimiterLabel(preview.delimiter)}-delimited`,
    `${preview.totalRows} row${preview.totalRows === 1 ? "" : "s"}`,
    `${preview.totalColumns} column${preview.totalColumns === 1 ? "" : "s"}`,
  ];

  if (!preview.totalRows || !previewColumns) {
    return (
      <div className="doc-inspector-table-panel">
        <div className="status-note">
          No tabular rows detected. Use edit mode to inspect the raw text.
        </div>
      </div>
    );
  }

  return (
    <div className="doc-inspector-table-panel">
      <div className="doc-inspector-table-meta">
        <span>{summaryBits.join(" | ")}</span>
        <label className="doc-inspector-table-toggle">
          <input
            type="checkbox"
            checked={useFirstRowAsHeader}
            onChange={(event) => setUseFirstRowAsHeader(event.target.checked)}
          />
          first row is header
        </label>
      </div>
      {preview.truncatedRows || preview.truncatedColumns ? (
        <div className="status-note">
          Preview limited to the first {preview.rows.length} row
          {preview.rows.length === 1 ? "" : "s"}
          {preview.truncatedColumns ? ` and ${preview.rows[0]?.length || previewColumns} columns` : ""}.
        </div>
      ) : null}
      <div className="doc-inspector-table-wrap">
        <table className="doc-inspector-table">
          <thead>
            <tr>
              <th className="doc-inspector-row-index" scope="col">
                row
              </th>
              {headerCells.map((cell, index) => (
                <th key={`header-${index}`} scope="col" title={cell}>
                  {cell || fallbackHeaders[index]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((row, rowIndex) => (
              <tr key={`row-${rowIndex}`}>
                <td className="doc-inspector-row-index">{rowIndex + rowOffset}</td>
                {headerCells.map((_, columnIndex) => {
                  const cell = row[columnIndex] || "";
                  return (
                    <td key={`cell-${rowIndex}-${columnIndex}`} title={cell}>
                      {cell}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default CsvTablePreview;
