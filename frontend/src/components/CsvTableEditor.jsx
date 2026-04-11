import React, { useEffect, useMemo, useState } from "react";

import {
  ensureRectangularRows,
  formatDelimiterLabel,
  parseCsvDocument,
  serializeCsvDocument,
  shouldUseFirstRowAsHeader,
} from "../utils/csvPreview";

const buildFallbackHeaders = (count) =>
  Array.from({ length: count }, (_, index) => `column ${index + 1}`);

const CsvTableEditor = ({ text = "", onChange, disabled = false }) => {
  const parsed = useMemo(() => parseCsvDocument(text), [text]);
  const [useFirstRowAsHeader, setUseFirstRowAsHeader] = useState(() =>
    shouldUseFirstRowAsHeader(parsed.rows),
  );

  useEffect(() => {
    setUseFirstRowAsHeader(shouldUseFirstRowAsHeader(parsed.rows));
  }, [parsed.rows]);

  const columnCount = useMemo(
    () => Math.max(parsed.totalColumns, ...parsed.rows.map((row) => row.length), 0),
    [parsed.rows, parsed.totalColumns],
  );
  const safeColumnCount = Math.max(columnCount, 1);
  const rectangularRows = useMemo(
    () => ensureRectangularRows(parsed.rows.length ? parsed.rows : [[""]], safeColumnCount),
    [parsed.rows, safeColumnCount],
  );
  const headerCells = useMemo(
    () => (useFirstRowAsHeader ? rectangularRows[0] || buildFallbackHeaders(safeColumnCount) : buildFallbackHeaders(safeColumnCount)),
    [rectangularRows, safeColumnCount, useFirstRowAsHeader],
  );
  const bodyRows = useMemo(
    () => (useFirstRowAsHeader ? rectangularRows.slice(1) : rectangularRows),
    [rectangularRows, useFirstRowAsHeader],
  );

  const commitRows = (nextRows) => {
    if (typeof onChange !== "function") return;
    onChange(serializeCsvDocument(nextRows, { delimiter: parsed.delimiter }));
  };

  const updateCell = (rowIndex, columnIndex, value) => {
    const nextRows = rectangularRows.map((row) => [...row]);
    while (nextRows.length <= rowIndex) {
      nextRows.push(Array.from({ length: safeColumnCount }, () => ""));
    }
    while ((nextRows[rowIndex] || []).length < safeColumnCount) {
      nextRows[rowIndex].push("");
    }
    nextRows[rowIndex][columnIndex] = value;
    commitRows(nextRows);
  };

  const addRow = () => {
    commitRows([
      ...rectangularRows,
      Array.from({ length: safeColumnCount }, () => ""),
    ]);
  };

  const addColumn = () => {
    commitRows(rectangularRows.map((row) => [...row, ""]));
  };

  return (
    <div className="doc-inspector-table-panel doc-inspector-table-editor">
      <div className="doc-inspector-table-meta">
        <span>
          {formatDelimiterLabel(parsed.delimiter)}-delimited | {rectangularRows.length} row
          {rectangularRows.length === 1 ? "" : "s"} | {safeColumnCount} column
          {safeColumnCount === 1 ? "" : "s"}
        </span>
        <label className="doc-inspector-table-toggle">
          <input
            type="checkbox"
            checked={useFirstRowAsHeader}
            onChange={(event) => setUseFirstRowAsHeader(event.target.checked)}
            disabled={disabled}
          />
          first row is header
        </label>
      </div>
      <div className="doc-inspector-table-editor-actions">
        <button type="button" onClick={addRow} disabled={disabled}>
          Add row
        </button>
        <button type="button" onClick={addColumn} disabled={disabled}>
          Add column
        </button>
      </div>
      <div className="doc-inspector-table-wrap">
        <table className="doc-inspector-table">
          <thead>
            <tr>
              <th className="doc-inspector-row-index" scope="col">
                row
              </th>
              {headerCells.map((cell, columnIndex) => (
                <th key={`header-${columnIndex}`} scope="col">
                  {useFirstRowAsHeader ? (
                    <input
                      type="text"
                      value={cell || ""}
                      onChange={(event) => updateCell(0, columnIndex, event.target.value)}
                      disabled={disabled}
                    />
                  ) : (
                    cell || `column ${columnIndex + 1}`
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((row, rowIndex) => {
              const actualRowIndex = useFirstRowAsHeader ? rowIndex + 1 : rowIndex;
              return (
                <tr key={`row-${actualRowIndex}`}>
                  <td className="doc-inspector-row-index">{actualRowIndex + 1}</td>
                  {row.map((cell, columnIndex) => (
                    <td key={`cell-${actualRowIndex}-${columnIndex}`}>
                      <input
                        type="text"
                        value={cell || ""}
                        onChange={(event) => updateCell(actualRowIndex, columnIndex, event.target.value)}
                        disabled={disabled}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default CsvTableEditor;
