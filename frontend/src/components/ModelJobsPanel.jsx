import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";

const AUTO_REFRESH_MS = 5000;

const formatBytes = (value) => {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) return "0 B";
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let num = value;
  let idx = 0;
  while (num >= 1024 && idx < units.length - 1) {
    num /= 1024;
    idx += 1;
  }
  const rounded = num >= 10 || idx === 0 ? Math.round(num) : Math.round(num * 10) / 10;
  return `${rounded} ${units[idx]}`;
};

const formatProgress = (job) => {
  const downloaded = typeof job?.downloaded === "number" ? job.downloaded : 0;
  const total = typeof job?.total === "number" ? job.total : 0;
  const percent =
    typeof job?.percent === "number" && Number.isFinite(job.percent)
      ? Math.max(0, Math.min(1, job.percent))
      : total > 0
        ? Math.max(0, Math.min(1, downloaded / total))
        : 0;
  const pct = Math.round(percent * 100);
  return `${pct}% · ${formatBytes(downloaded)} / ${formatBytes(total)}`;
};

const formatUpdated = (value) => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  try {
    return new Date(value * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "--";
  }
};

const ModelJobsPanel = () => {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [busyJobId, setBusyJobId] = useState("");

  const refreshJobs = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await axios.get("/api/models/jobs", {
        params: { limit: 50, include_finished: true },
      });
      setJobs(Array.isArray(response?.data?.jobs) ? response.data.jobs : []);
    } catch (err) {
      setJobs([]);
      setError("Failed to load model jobs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshJobs();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(() => {
      refreshJobs();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh]);

  const activeCount = useMemo(
    () => jobs.filter((job) => ["running", "paused", "error"].includes(String(job.status || ""))).length,
    [jobs],
  );

  const runAction = async (jobId, action) => {
    if (!jobId) return;
    setBusyJobId(`${jobId}:${action}`);
    setError("");
    try {
      await axios.post(`/api/models/jobs/${jobId}/${action}`);
      await refreshJobs();
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err?.message || `Failed to ${action} job`;
      setError(String(detail));
    } finally {
      setBusyJobId("");
    }
  };

  return (
    <div className="celery-panel">
      <div
        className="celery-header"
        style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}
      >
        <div className="inline-flex" style={{ alignItems: "center", gap: 8 }}>
          <h3 style={{ margin: 0 }}>Model jobs</h3>
          <span className="status-note">
            {activeCount > 0 ? `${activeCount} active` : "idle"}
          </span>
        </div>
        <div className="inline-flex">
          <label className="inline-flex" style={{ gap: 6 }} title="Auto-refresh every ~5s">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(!!event.target.checked)}
            />
            Auto-refresh
          </label>
          <button type="button" onClick={refreshJobs} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="alert" role="status">
          {error}
        </div>
      ) : null}

      <div className="celery-table-wrap" style={{ marginTop: 8 }}>
        <table className="celery-table" aria-label="model jobs">
          <thead>
            <tr>
              <th>model</th>
              <th>status</th>
              <th>progress</th>
              <th>updated</th>
              <th>path</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", opacity: 0.7 }}>
                  no model jobs
                </td>
              </tr>
            ) : (
              jobs.map((job) => {
                const status = String(job.status || "");
                const canPause = status === "running";
                const canResume = status === "paused" || status === "error";
                const canCancel = ["running", "paused", "error"].includes(status);
                return (
                  <tr key={job.id || `${job.model}-${job.path}`}>
                    <td title={job.model}>{job.model || "--"}</td>
                    <td>{status || "--"}</td>
                    <td>{formatProgress(job)}</td>
                    <td>{formatUpdated(job.updated_at || job.started_at)}</td>
                    <td title={job.path}>{job.path || "--"}</td>
                    <td>
                      <div className="inline-flex" style={{ gap: 6, justifyContent: "flex-end" }}>
                        {canPause ? (
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => runAction(job.id, "pause")}
                            disabled={busyJobId === `${job.id}:pause`}
                          >
                            Pause
                          </button>
                        ) : null}
                        {canResume ? (
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => runAction(job.id, "resume")}
                            disabled={busyJobId === `${job.id}:resume`}
                          >
                            Resume
                          </button>
                        ) : null}
                        {canCancel ? (
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => runAction(job.id, "cancel")}
                            disabled={busyJobId === `${job.id}:cancel`}
                          >
                            Cancel
                          </button>
                        ) : null}
                        {!canPause && !canResume && !canCancel ? (
                          <span style={{ opacity: 0.6 }}>--</span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ModelJobsPanel;
