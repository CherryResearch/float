import React from "react";

const BrowserSessionDialog = ({
  isOpen,
  session,
  fallbackSessionId = "",
  pendingAction = "",
  error = "",
  navigateDraft = "",
  setNavigateDraft,
  typeDraft = "",
  setTypeDraft,
  keyDraft = "",
  setKeyDraft,
  onClose,
  onObserve,
  onNavigate,
  onType,
  onKeypress,
  onScreenshotClick,
  idPrefix = "browser-session",
}) => {
  if (!isOpen) return null;
  const screenshotUrl = session?.attachment?.url || "";
  const busy = Boolean(pendingAction);

  return (
    <div className="browser-session-overlay" onClick={onClose}>
      <section
        className="browser-session-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Browser session controls"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="browser-session-header">
          <div className="browser-session-header-copy">
            <h3>browser session</h3>
            <span>
              {session?.sessionId || fallbackSessionId}
              {session?.currentUrl ? ` | ${session.currentUrl}` : ""}
            </span>
          </div>
          <div className="browser-session-header-actions">
            <button
              type="button"
              className="agent-card-control-btn"
              onClick={onObserve}
              disabled={!session?.sessionId || busy}
            >
              {pendingAction === "observe" ? "refreshing" : "refresh"}
            </button>
            <button
              type="button"
              className="agent-card-control-btn danger"
              onClick={onClose}
            >
              close
            </button>
          </div>
        </header>
        <div className="browser-session-body">
          <div className="browser-session-stage">
            {screenshotUrl ? (
              <>
                <img
                  src={screenshotUrl}
                  alt={session?.attachment?.name || "Browser session screenshot"}
                  className={`browser-session-image${busy ? " is-busy" : ""}`}
                  onClick={onScreenshotClick}
                />
                <div className="browser-session-stage-note">
                  click the screenshot to send a click to the browser session
                </div>
              </>
            ) : (
              <div className="browser-session-empty">
                No browser screenshot yet. Refresh to capture the current page.
              </div>
            )}
          </div>
          <div className="browser-session-controls">
            <form className="browser-session-form" onSubmit={onNavigate}>
              <label htmlFor={`${idPrefix}-url`}>navigate</label>
              <div className="browser-session-form-row">
                <input
                  id={`${idPrefix}-url`}
                  type="url"
                  value={navigateDraft}
                  onChange={(event) => setNavigateDraft?.(event.target.value)}
                  placeholder="https://example.com"
                />
                <button type="submit" disabled={!session?.sessionId || busy}>
                  {pendingAction === "navigate" ? "opening" : "open"}
                </button>
              </div>
            </form>
            <form className="browser-session-form" onSubmit={onType}>
              <label htmlFor={`${idPrefix}-type`}>type</label>
              <div className="browser-session-form-row">
                <input
                  id={`${idPrefix}-type`}
                  type="text"
                  value={typeDraft}
                  onChange={(event) => setTypeDraft?.(event.target.value)}
                  placeholder="Type into the focused field"
                />
                <button type="submit" disabled={!session?.sessionId || busy}>
                  {pendingAction === "type" ? "typing" : "send"}
                </button>
              </div>
            </form>
            <form className="browser-session-form" onSubmit={onKeypress}>
              <label htmlFor={`${idPrefix}-keys`}>keypress</label>
              <div className="browser-session-form-row">
                <input
                  id={`${idPrefix}-keys`}
                  type="text"
                  value={keyDraft}
                  onChange={(event) => setKeyDraft?.(event.target.value)}
                  placeholder="Enter or Control+L"
                />
                <button type="submit" disabled={!session?.sessionId || busy}>
                  {pendingAction === "keypress" ? "sending" : "press"}
                </button>
              </div>
            </form>
            {session?.summary && (
              <div className="browser-session-status" role="status">
                {session.summary}
              </div>
            )}
            {error && (
              <div className="browser-session-error" role="alert">
                {error}
              </div>
            )}
            {!session?.sessionId && (
              <div className="browser-session-empty">
                This browser session is no longer available here.
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
};

export default BrowserSessionDialog;
