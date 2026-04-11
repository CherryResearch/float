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

const basenameFromPath = (value) => {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  const parts = trimmed.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
};

const screenshotUrlFromPath = (value) => {
  const name = basenameFromPath(value);
  return name ? `/api/computer/screenshots/${encodeURIComponent(name)}` : "";
};

const captureUrlFromId = (value) => {
  const captureId = getString(value);
  return captureId ? `/api/captures/${encodeURIComponent(captureId)}/content` : "";
};

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

const getPreferredAttachment = (payload, data, session) => {
  const candidates = [
    payload?.attachment,
    Array.isArray(payload?.attachments) ? payload.attachments[0] : null,
    Array.isArray(payload?.image_attachments) ? payload.image_attachments[0] : null,
    data?.attachment,
    Array.isArray(data?.attachments) ? data.attachments[0] : null,
    Array.isArray(data?.image_attachments) ? data.image_attachments[0] : null,
    session?.attachment,
  ];
  return candidates.find((item) => isPlainObject(item)) || null;
};

const normalizeAttachmentCandidate = (candidate) => {
  if (!isPlainObject(candidate)) return null;
  const captureId = getString(candidate.capture_id || candidate.captureId || candidate.id || "");
  const url =
    getString(candidate.url || candidate.href || "") || captureUrlFromId(captureId);
  if (!url) return null;
  return {
    url,
    name:
      getString(candidate.name || candidate.filename || candidate.title || "") ||
      basenameFromPath(url) ||
      "capture.png",
    type: getString(candidate.type || candidate.content_type || ""),
    captureId,
    source: getString(
      candidate.capture_source || candidate.source || candidate.origin || "",
    ),
  };
};

const normalizeCaptureItem = (candidate) => {
  if (!isPlainObject(candidate)) return null;
  const attachment = normalizeAttachmentCandidate(
    candidate.attachment || candidate.attachment_ref || candidate,
  );
  const captureId = getString(
    candidate.capture_id || candidate.captureId || candidate.id || attachment?.captureId || "",
  );
  const filename =
    getString(candidate.filename || candidate.name || attachment?.name || "") ||
    basenameFromPath(attachment?.url || "") ||
    (captureId ? `${captureId}.png` : "");
  if (!attachment && !filename && !captureId) return null;
  return {
    attachment:
      attachment ||
      normalizeAttachmentCandidate({
        capture_id: captureId,
        filename,
      }),
    captureId,
    filename,
    source: getString(
      candidate.capture_source || candidate.source || attachment?.source || "",
    ),
    transient:
      typeof candidate.transient === "boolean" ? candidate.transient : null,
    promoted: typeof candidate.promoted === "boolean" ? candidate.promoted : null,
  };
};

export const extractComputerPayload = (payload, toolName) => {
  const label = typeof toolName === "string" ? toolName.toLowerCase() : "";
  if (!payload || typeof payload !== "object") return null;
  if (!label.startsWith("computer.") && label !== "open_url") return null;
  const data = isPlainObject(payload.data) ? payload.data : payload;
  const session =
    (isPlainObject(payload.session) && payload.session) ||
    (isPlainObject(data.session) && data.session) ||
    null;
  const baseAttachment = getPreferredAttachment(payload, data, session);
  const summary = getString(payload.summary || data.summary || "");
  const currentUrl = getString(
    data.current_url || session?.current_url || payload.current_url || "",
  );
  const activeWindow = getString(
    data.active_window || session?.active_window || payload.active_window || "",
  );
  const runtime = getString(
    data.runtime || session?.runtime || payload.runtime || "",
  );
  const sessionId = getString(
    data.id ||
      data.session_id ||
      session?.id ||
      session?.session_id ||
      payload.id ||
      payload.session_id ||
      "",
  );
  const lastScreenshotPath = getString(
    data.last_screenshot_path ||
      session?.last_screenshot_path ||
      payload.last_screenshot_path ||
      "",
  );
  const captureId = getString(
    baseAttachment?.capture_id ||
      data.capture_id ||
      session?.capture_id ||
      payload.capture_id ||
      "",
  );
  const derivedAttachment = (() => {
    if (baseAttachment) return { ...baseAttachment };
    const captureUrl = captureUrlFromId(captureId);
    if (captureUrl) {
      return {
        url: captureUrl,
        name: captureId,
        capture_id: captureId,
      };
    }
    const screenshotUrl = screenshotUrlFromPath(lastScreenshotPath);
    if (screenshotUrl) {
      return {
        url: screenshotUrl,
        name: basenameFromPath(lastScreenshotPath) || "screenshot.png",
      };
    }
    return null;
  })();
  if (
    !derivedAttachment &&
    !summary &&
    !currentUrl &&
    !activeWindow &&
    !sessionId &&
    !runtime
  ) {
    return null;
  }
  return {
    attachment: derivedAttachment,
    summary,
    currentUrl,
    activeWindow,
    runtime,
    sessionId,
    lastScreenshotPath,
    captureId,
    session,
  };
};

export const extractCapturePayload = (payload, toolName) => {
  const label = typeof toolName === "string" ? toolName.toLowerCase() : "";
  if (!payload || typeof payload !== "object") return null;
  if (!label.startsWith("capture.") && label !== "camera.capture") return null;
  const data = isPlainObject(payload.data) ? payload.data : payload;
  const summary = getString(payload.summary || data.summary || payload.message || data.message || "");
  const directCandidates = [
    payload.capture,
    data.capture,
    payload.attachment,
    data.attachment,
    label === "camera.capture" ? data : null,
  ]
    .map(normalizeCaptureItem)
    .filter(Boolean);
  const listedItems = [
    ...(Array.isArray(payload.captures) ? payload.captures : []),
    ...(Array.isArray(data.captures) ? data.captures : []),
  ]
    .map(normalizeCaptureItem)
    .filter(Boolean);
  const items = [...directCandidates, ...listedItems].filter(
    (item, index, arr) =>
      arr.findIndex(
        (other) =>
          other.captureId &&
          item.captureId &&
          other.captureId === item.captureId,
      ) === index || !item.captureId,
  );
  if (!summary && !items.length) return null;
  return {
    summary,
    items,
    count:
      getNumber(payload.count ?? data.count) ??
      (items.length > 0 ? items.length : null),
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

const renderComputerPayload = (computer, onOpenComputerSession = null) => (
  <div className="tool-computer-card">
    {computer.attachment?.url && (
      <a
        className="tool-computer-link"
        href={computer.attachment.url}
        target="_blank"
        rel="noopener noreferrer"
      >
        <img
          src={computer.attachment.url}
          alt={computer.attachment.name || "Computer screenshot"}
          className="tool-computer-image"
          loading="lazy"
        />
      </a>
    )}
    {computer.summary && (
      <div className="tool-computer-summary">{computer.summary}</div>
    )}
    {(computer.sessionId || computer.runtime) && (
      <div className="tool-computer-session-meta">
        {computer.sessionId && <span>session {computer.sessionId}</span>}
        {computer.runtime && <span>{computer.runtime}</span>}
      </div>
    )}
    {(computer.currentUrl || computer.activeWindow) && (
      <div className="tool-computer-meta">
        {computer.currentUrl && <span>{computer.currentUrl}</span>}
        {computer.activeWindow && <span>{computer.activeWindow}</span>}
      </div>
    )}
    {typeof onOpenComputerSession === "function" &&
      computer.runtime === "browser" &&
      computer.sessionId && (
      <button
        type="button"
        className="tool-computer-expand-btn"
        onClick={() => onOpenComputerSession(computer)}
      >
        expand browser
      </button>
    )}
  </div>
);

const renderCapturePayload = (capture) => (
  <div className="tool-capture-card">
    {capture.summary && (
      <div className="tool-computer-summary">{capture.summary}</div>
    )}
    {typeof capture.count === "number" && capture.count > 1 && (
      <div className="tool-computer-session-meta">
        <span>{capture.count} captures</span>
      </div>
    )}
    {capture.items.length > 0 ? (
      <div className="tool-list">
        {capture.items.map((item, idx) => (
          <div key={item.captureId || `capture-${idx}`} className="tool-list-item">
            {item.attachment?.url && (
              <a
                className="tool-computer-link"
                href={item.attachment.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <img
                  src={item.attachment.url}
                  alt={item.filename || item.attachment.name || "Captured image"}
                  className="tool-computer-image"
                  loading="lazy"
                />
              </a>
            )}
            {(item.filename || item.captureId) && (
              <div className="tool-computer-session-meta">
                {item.filename && <span>{item.filename}</span>}
                {item.captureId && <span>{item.captureId}</span>}
              </div>
            )}
            {(item.source || item.promoted !== null || item.transient !== null) && (
              <div className="tool-computer-meta">
                {item.source && <span>{item.source}</span>}
                {item.promoted !== null && (
                  <span>{item.promoted ? "promoted" : "transient"}</span>
                )}
                {item.transient === false && item.promoted === null && <span>saved</span>}
              </div>
            )}
          </div>
        ))}
      </div>
    ) : (
      <div className="tool-payload-empty">No captures.</div>
    )}
  </div>
);

export const summarizeToolPayload = (value, toolName = null) => {
  const normalized = normalizeToolPayload(value);
  if (normalized === null || typeof normalized === "undefined") return "";
  const { payload, message } = unwrapToolOutcome(normalized);
  if (message) return message;
  const computer = extractComputerPayload(payload, toolName);
  if (computer) {
    return computer.summary || computer.currentUrl || computer.activeWindow || "computer update";
  }
  const capture = extractCapturePayload(payload, toolName);
  if (capture) {
    if (capture.summary) return capture.summary;
    if (capture.items[0]?.filename) return capture.items[0].filename;
    if (typeof capture.count === "number") return `${capture.count} capture(s)`;
    return "capture update";
  }
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
  onOpenComputerSession = null,
}) => {
  const normalized = normalizeToolPayload(value);
  if (normalized === null || typeof normalized === "undefined") return null;
  const { payload, status, ok, message } = unwrapToolOutcome(normalized);
  const search = extractSearchPayload(payload, toolName);
  const computer = extractComputerPayload(payload, toolName);
  const capture = extractCapturePayload(payload, toolName);
  const statusKey = status ? String(status).toLowerCase() : "";
  const showStatus = !!status;
  const classes = [
    "tool-payload",
    `tool-payload-${kind}`,
    compact ? "compact" : "",
    computer?.attachment?.url || capture?.items.some((item) => item.attachment?.url)
      ? "tool-payload-has-media"
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (() => {
    if (computer) return renderComputerPayload(computer, onOpenComputerSession);
    if (capture) return renderCapturePayload(capture);
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
