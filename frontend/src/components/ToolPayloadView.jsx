import React from "react";

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const maybeParseJson = (value) => {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
};

const normalizeToolPayload = (value) => {
  const parsed = maybeParseJson(value);
  return parsed;
};

const unwrapToolOutcome = (value) => {
  if (!isPlainObject(value)) {
    return { payload: value, status: null, ok: null, message: null };
  }
  const hasStatus = typeof value.status === "string" && value.status.trim();
  const hasWrapperKeys = "data" in value || "ok" in value || "message" in value;
  if (!hasStatus || !hasWrapperKeys) {
    return { payload: value, status: null, ok: null, message: null };
  }
  const rawPayload = Object.prototype.hasOwnProperty.call(value, "data")
    ? value.data
    : value;
  const normalizedPayload = maybeParseJson(rawPayload);
  return {
    payload: normalizedPayload,
    status: value.status,
    ok: typeof value.ok === "boolean" ? value.ok : null,
    message: typeof value.message === "string" ? value.message : null,
  };
};

const stringifyValue = (value) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const getString = (value) =>
  typeof value === "string" && value.trim() ? value.trim() : "";

const getNumber = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const domainFromUrl = (url) => {
  if (!url || typeof url !== "string") return "";
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
};

const normalizeSearchItem = (item) => {
  if (!isPlainObject(item)) return null;
  const title = getString(item.title || item.name || item.label || "");
  const url = getString(item.url || item.link || item.href || "");
  const snippet = getString(item.snippet || item.description || item.summary || item.text || "");
  const resolvedTitle = title || url || "Untitled";
  return {
    title: resolvedTitle,
    url: url || "",
    snippet,
    domain: domainFromUrl(url),
  };
};

const extractSearchPayload = (payload, toolName) => {
  if (!payload || typeof payload !== "object") return null;
  const results = Array.isArray(payload.results) ? payload.results : null;
  const query = getString(payload.query || payload.search || payload.q || "");
  const region = getString(payload.region || "");
  const maxResults = getNumber(payload.max_results || payload.maxResults);
  const count = getNumber(payload.count);
  const warning = getString(payload.warning || payload.warn || "");
  const toolLabel = typeof toolName === "string" ? toolName.toLowerCase() : "";
  const isSearchTool = toolLabel.includes("search");
  const hasSearchShape =
    Array.isArray(results) &&
    results.some(
      (item) =>
        isPlainObject(item) &&
        (item.title || item.name || item.label || item.url || item.link || item.href),
    );
  if (!isSearchTool && !query && !hasSearchShape) return null;

  const items = Array.isArray(results)
    ? results.map(normalizeSearchItem).filter(Boolean)
    : [];

  return {
    query,
    region,
    maxResults,
    count,
    warning,
    items,
    hasResults: items.length > 0,
  };
};

const renderKeyValueList = (payload) => {
  const entries = Object.entries(payload || {}).filter(([, val]) => typeof val !== "undefined");
  if (!entries.length) {
    return <div className="tool-payload-empty">No details.</div>;
  }
  return (
    <dl className="tool-kv">
      {entries.map(([key, val]) => (
        <div key={key} className="tool-kv-row">
          <dt>{key}</dt>
          <dd>{stringifyValue(val)}</dd>
        </div>
      ))}
    </dl>
  );
};

const renderArrayPayload = (payload) => {
  if (!Array.isArray(payload) || !payload.length) {
    return <div className="tool-payload-empty">No items.</div>;
  }
  const allScalars = payload.every(
    (item) =>
      item === null ||
      ["string", "number", "boolean"].includes(typeof item),
  );
  if (allScalars) {
    return (
      <ul className="tool-list">
        {payload.map((item, idx) => (
          <li key={`tool-list-${idx}`}>{stringifyValue(item)}</li>
        ))}
      </ul>
    );
  }
  return (
    <div className="tool-list">
      {payload.map((item, idx) => (
        <div key={`tool-list-${idx}`} className="tool-list-item">
          {isPlainObject(item) ? renderKeyValueList(item) : stringifyValue(item)}
        </div>
      ))}
    </div>
  );
};

const renderTextBody = (payload) => {
  const text = stringifyValue(payload);
  if (!text) return null;
  return <div className="tool-payload-text">{text}</div>;
};

const renderSearchResults = (search) => {
  const metaParts = [];
  if (typeof search.count === "number") metaParts.push(`${search.count} results`);
  if (typeof search.maxResults === "number") metaParts.push(`max ${search.maxResults}`);
  if (search.region) metaParts.push(search.region);

  return (
    <>
      {search.query && (
        <div className="tool-search-query">
          <em>Search:</em> {search.query}
        </div>
      )}
      {metaParts.length > 0 && (
        <div className="tool-search-meta">{metaParts.join(" | ")}</div>
      )}
      {search.warning && (
        <div className="tool-search-warning">{search.warning}</div>
      )}
      {search.hasResults ? (
        <ol className="tool-search-results">
          {search.items.map((item, idx) => (
            <li key={`tool-search-${idx}`} className="tool-search-result">
              <div className="tool-search-title">
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noopener noreferrer">
                    {item.title}
                  </a>
                ) : (
                  <span>{item.title}</span>
                )}
                {item.domain && (
                  <span className="tool-search-domain">{item.domain}</span>
                )}
              </div>
              {item.snippet && (
                <div className="tool-search-snippet">{item.snippet}</div>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <div className="tool-payload-empty">No results.</div>
      )}
    </>
  );
};

export const summarizeToolPayload = (value, toolName = null) => {
  const normalized = normalizeToolPayload(value);
  if (normalized === null || typeof normalized === "undefined") return "";
  const { payload, message } = unwrapToolOutcome(normalized);
  if (message) return message;
  const search = extractSearchPayload(payload, toolName);
  if (search && (search.query || search.items.length)) {
    const label = search.query ? `Search: ${search.query}` : "Search results";
    const firstTitle = search.items[0]?.title || "";
    return firstTitle ? `${label} -> ${firstTitle}` : label;
  }
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    if (payload.error) return `error: ${payload.error}`;
    if (payload.message) return String(payload.message);
  }
  return stringifyValue(payload);
};

const ToolPayloadView = ({
  value,
  kind = "result",
  toolName,
  compact = false,
  label = null,
}) => {
  const normalized = normalizeToolPayload(value);
  if (normalized === null || typeof normalized === "undefined") return null;
  const { payload, status, ok, message } = unwrapToolOutcome(normalized);
  const search = extractSearchPayload(payload, toolName);
  const statusKey = status ? String(status).toLowerCase() : "";
  const showStatus = !!status;
  const classes = [
    "tool-payload",
    `tool-payload-${kind}`,
    compact ? "compact" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (() => {
    if (search) return renderSearchResults(search);
    if (Array.isArray(payload)) return renderArrayPayload(payload);
    if (isPlainObject(payload)) return renderKeyValueList(payload);
    return renderTextBody(payload);
  })();

  return (
    <section className={classes}>
      {label && <div className="tool-payload-label">{label}</div>}
      {showStatus && (
        <div className={`tool-payload-status status-${statusKey}`}>
          <span>{status}</span>
          {typeof ok === "boolean" && (
            <span className="tool-payload-status-note">
              {ok ? "ok" : "not ok"}
            </span>
          )}
        </div>
      )}
      {message && <div className="tool-payload-message">{message}</div>}
      {content}
    </section>
  );
};

export default ToolPayloadView;
