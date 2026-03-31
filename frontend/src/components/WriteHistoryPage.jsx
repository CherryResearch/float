import React from "react";
import { Link } from "react-router-dom";
import ActionHistoryPanel from "./ActionHistoryPanel";
import "../styles/Settings.css";
import "../styles/WorkHistoryPage.css";

const WriteHistoryPage = ({
  actions = [],
  backendReady = true,
  loading = false,
  onRefresh,
}) => {
  return (
    <div className="work-history-page settings-container">
      <section className="settings-card" aria-label="Work history page">
        <div className="settings-card-header">
          <div>
            <h2>Work History</h2>
            <p className="settings-card-copy">
              A static view of the current reversible write-history cache outside the agent
              console.
            </p>
          </div>
          <div className="inline-flex work-history-page-actions">
            <Link
              to="/settings"
              className="icon-btn work-history-page-link"
              style={{ marginTop: 0 }}
            >
              Back to settings
            </Link>
            <button
              type="button"
              className="icon-btn"
              onClick={() => onRefresh?.()}
              disabled={!backendReady || loading}
              style={{ marginTop: 0 }}
            >
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>

        <div className="settings-section">
          {!loading && (!Array.isArray(actions) || actions.length === 0) ? (
            <p className="status-note">
              No tracked writes are cached right now.
            </p>
          ) : null}
          <ActionHistoryPanel
            actions={actions}
            backendReady={backendReady}
            onRefresh={onRefresh}
          />
        </div>
      </section>
    </div>
  );
};

export default WriteHistoryPage;
