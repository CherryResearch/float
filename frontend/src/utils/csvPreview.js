export const TABULAR_DOC_EXTENSIONS = new Set(["csv", "tsv", "tab"]);

const DEFAULT_DELIMITER = ",";
const DELIMITER_CANDIDATES = [",", "\t", ";", "|"];

const isLikelyValue = (value) => {
  const trimmed = String(value || "").trim();
  if (!trimmed) return false;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return true;
  if (/^(true|false|null)$/i.test(trimmed)) return true;
  if (/^\d{4}-\d{2}-\d{2}(?:[t\s]\d{2}:\d{2}(?::\d{2})?)?/i.test(trimmed)) return true;
  return false;
};

const finalizeField = (previewRow, currentCell, currentFieldCount, maxColumns) => {
  const nextFieldCount = currentFieldCount + 1;
  if (previewRow.length < maxColumns) {
    previewRow.push(currentCell);
  }
  return nextFieldCount;
};

const scoreDelimiter = (text, delimiter) => {
  let rowFieldCount = 0;
  let rows = 0;
  let consistentRows = 0;
  let maxFields = 0;
  let currentFieldCount = 0;
  let currentCell = "";
  let inQuotes = false;
  let touchedRow = false;
  let previousFieldCount = null;

  const commitRow = () => {
    if (!touchedRow && !currentCell) {
      currentFieldCount = 0;
      currentCell = "";
      return;
    }
    const fieldCount = currentFieldCount + 1;
    rows += 1;
    rowFieldCount += fieldCount;
    maxFields = Math.max(maxFields, fieldCount);
    if (previousFieldCount !== null && previousFieldCount === fieldCount) {
      consistentRows += 1;
    }
    previousFieldCount = fieldCount;
    currentFieldCount = 0;
    currentCell = "";
    touchedRow = false;
  };

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      touchedRow = true;
      if (inQuotes && text[index + 1] === '"') {
        currentCell += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (!inQuotes && char === delimiter) {
      touchedRow = true;
      currentFieldCount += 1;
      currentCell = "";
      continue;
    }
    if (!inQuotes && (char === "\n" || char === "\r")) {
      if (char === "\r" && text[index + 1] === "\n") {
        index += 1;
      }
      commitRow();
      if (rows >= 12) break;
      continue;
    }
    currentCell += char;
    if (char.trim()) touchedRow = true;
  }

  if (rows < 12) {
    commitRow();
  }

  if (!rows || maxFields <= 1) {
    return {
      delimiter,
      rows,
      averageFields: 0,
      maxFields,
      consistency: 0,
      score: 0,
    };
  }

  const averageFields = rowFieldCount / rows;
  const consistency = consistentRows / Math.max(rows - 1, 1);
  return {
    delimiter,
    rows,
    averageFields,
    maxFields,
    consistency,
    score: averageFields * (1 + consistency),
  };
};

export const detectDelimiter = (text) => {
  const normalized = String(text || "").replace(/^\uFEFF/, "");
  const scores = DELIMITER_CANDIDATES.map((delimiter) => scoreDelimiter(normalized, delimiter));
  const best = scores.reduce((winner, current) => {
    if (!winner) return current;
    if (current.score > winner.score) return current;
    if (current.score === winner.score && current.maxFields > winner.maxFields) return current;
    return winner;
  }, null);
  return best && best.score > 0 ? best.delimiter : DEFAULT_DELIMITER;
};

export const parseCsvPreview = (text, options = {}) => {
  const normalized = String(text || "").replace(/^\uFEFF/, "");
  const maxRows = Number.isFinite(options.maxRows) ? Math.max(1, options.maxRows) : 200;
  const maxColumns = Number.isFinite(options.maxColumns) ? Math.max(1, options.maxColumns) : 40;
  const delimiter = options.delimiter || detectDelimiter(normalized);

  const rows = [];
  let totalRows = 0;
  let totalColumns = 0;
  let truncatedRows = false;
  let truncatedColumns = false;
  let inQuotes = false;
  let currentCell = "";
  let currentFieldCount = 0;
  let previewRow = [];
  let touchedRow = false;

  const commitRow = () => {
    if (!touchedRow && !currentCell) {
      currentCell = "";
      currentFieldCount = 0;
      previewRow = [];
      return;
    }
    const fieldCount = finalizeField(previewRow, currentCell, currentFieldCount, maxColumns);
    if (fieldCount > maxColumns) truncatedColumns = true;
    totalRows += 1;
    totalColumns = Math.max(totalColumns, fieldCount);
    if (rows.length < maxRows) {
      rows.push(previewRow);
    } else {
      truncatedRows = true;
    }
    currentCell = "";
    currentFieldCount = 0;
    previewRow = [];
    touchedRow = false;
  };

  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
    if (char === '"') {
      touchedRow = true;
      if (inQuotes && normalized[index + 1] === '"') {
        currentCell += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (!inQuotes && char === delimiter) {
      touchedRow = true;
      currentFieldCount = finalizeField(previewRow, currentCell, currentFieldCount, maxColumns);
      currentCell = "";
      continue;
    }
    if (!inQuotes && (char === "\n" || char === "\r")) {
      if (char === "\r" && normalized[index + 1] === "\n") {
        index += 1;
      }
      commitRow();
      continue;
    }
    currentCell += char;
    if (char.trim()) touchedRow = true;
  }

  commitRow();

  return {
    delimiter,
    rows,
    totalRows,
    totalColumns,
    truncatedRows,
    truncatedColumns,
  };
};

export const parseCsvDocument = (text, options = {}) => {
  const normalized = String(text || "").replace(/^\uFEFF/, "");
  const delimiter = options.delimiter || detectDelimiter(normalized);

  const rows = [];
  let totalColumns = 0;
  let inQuotes = false;
  let currentCell = "";
  let currentRow = [];
  let touchedRow = false;

  const commitCell = () => {
    currentRow.push(currentCell);
    currentCell = "";
  };

  const commitRow = () => {
    if (!touchedRow && !currentCell && currentRow.length === 0) {
      currentCell = "";
      currentRow = [];
      return;
    }
    commitCell();
    totalColumns = Math.max(totalColumns, currentRow.length);
    rows.push(currentRow);
    currentCell = "";
    currentRow = [];
    touchedRow = false;
  };

  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
    if (char === '"') {
      touchedRow = true;
      if (inQuotes && normalized[index + 1] === '"') {
        currentCell += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (!inQuotes && char === delimiter) {
      touchedRow = true;
      commitCell();
      continue;
    }
    if (!inQuotes && (char === "\n" || char === "\r")) {
      if (char === "\r" && normalized[index + 1] === "\n") {
        index += 1;
      }
      commitRow();
      continue;
    }
    currentCell += char;
    if (char.trim()) touchedRow = true;
  }

  commitRow();

  return {
    delimiter,
    rows,
    totalRows: rows.length,
    totalColumns,
  };
};

export const ensureRectangularRows = (rows, columnCount) =>
  (Array.isArray(rows) ? rows : []).map((row) => {
    const cells = Array.isArray(row) ? [...row] : [];
    while (cells.length < columnCount) {
      cells.push("");
    }
    return cells.slice(0, columnCount);
  });

const needsCsvQuotes = (value, delimiter) =>
  value.includes('"') || value.includes("\n") || value.includes("\r") || value.includes(delimiter);

export const serializeCsvDocument = (rows, options = {}) => {
  const delimiter = options.delimiter || DEFAULT_DELIMITER;
  const safeRows = Array.isArray(rows) ? rows : [];
  return safeRows
    .map((row) =>
      (Array.isArray(row) ? row : []).map((value) => {
        const text = String(value ?? "");
        if (!needsCsvQuotes(text, delimiter)) return text;
        return `"${text.replace(/"/g, '""')}"`;
      }).join(delimiter),
    )
    .join("\n");
};

export const shouldUseFirstRowAsHeader = (rows) => {
  if (!Array.isArray(rows) || rows.length < 2) return false;
  const firstRow = rows[0].map((cell) => String(cell || "").trim()).filter(Boolean);
  if (!firstRow.length) return false;
  const uniqueColumns = new Set(firstRow.map((cell) => cell.toLowerCase()));
  const headerishCount = firstRow.filter((cell) => !isLikelyValue(cell)).length;
  return uniqueColumns.size === firstRow.length && headerishCount >= Math.ceil(firstRow.length / 2);
};

export const formatDelimiterLabel = (delimiter) => {
  if (delimiter === "\t") return "tab";
  if (delimiter === ";") return "semicolon";
  if (delimiter === "|") return "pipe";
  return "comma";
};
