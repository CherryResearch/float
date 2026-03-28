import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import "../styles/ProgressBar.css";

const STORAGE_KEY = "modelDownloadJobs";
const CHANNEL = "model-download";
const RECENT_MS = 12_000;

const loadJobs = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
};

const saveJobs = (jobs) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs));
};

const humanBytes = (bytes, total) => {
  const format = (n) => {
    if (!n) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(n) / Math.log(k));
    return `${parseFloat((n / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };
  if (total) return `${format(bytes)} / ${format(total)}`;
  return format(bytes);
};

const OPEN_DELAY_MS = 300;
const CLOSE_DELAY_MS = 1200;
const OPEN_GRACE_MS = 400;

const detectComposerSurface = () => {
  if (typeof document === "undefined") return "none";
  if (document.querySelector(".input-box")) return "input-box";
  if (document.querySelector(".open-entry-btn")) return "entry-button";
  return "none";
};

const DownloadTray = () => {
  const [jobs, setJobs] = useState(loadJobs());
  const [expanded, setExpanded] = useState(
    localStorage.getItem("downloadTrayExpanded") === "true",
  );
  const [recentVisible, setRecentVisible] = useState(false);
  const [composerSurface, setComposerSurface] = useState(detectComposerSurface);

  const bcRef = useRef(null);
  const pollersRef = useRef({});
  const hoverTimerRef = useRef(null);
  const lastExpandedAtRef = useRef(0);
  const recentTimerRef = useRef(null);
  const didInitRef = useRef(false);

  useEffect(() => {
    if (typeof BroadcastChannel !== "function") {
      return undefined;
    }
    const bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = (e) => {
      const { type, payload } = e.data || {};
      if (type === "jobs:update") setJobs(payload || []);
      if (type === "tray:toggle") setExpanded(!!payload);
    };
    bcRef.current = bc;
    return () => bc.close();
  }, []);

  useEffect(() => {
    saveJobs(jobs);
    bcRef.current?.postMessage({ type: "jobs:update", payload: jobs });
  }, [jobs]);

  useEffect(() => {
    localStorage.setItem("downloadTrayExpanded", String(expanded));
    bcRef.current?.postMessage({ type: "tray:toggle", payload: expanded });
    if (expanded) lastExpandedAtRef.current = Date.now();
  }, [expanded]);

  const bumpRecent = () => {
    if (recentTimerRef.current) {
      clearTimeout(recentTimerRef.current);
      recentTimerRef.current = null;
    }
    setRecentVisible(true);
    recentTimerRef.current = setTimeout(() => setRecentVisible(false), RECENT_MS);
  };

  useEffect(() => {
    jobs.forEach((job) => {
      const id = job.id;
      const active = ["running", "paused"].includes(job.status);
      const hasPoller = !!pollersRef.current[id];
      if (active && !hasPoller) {
        pollersRef.current[id] = setInterval(async () => {
          try {
            const r = await axios.get(`/api/models/jobs/${id}`);
            const data = r.data?.job || {};
            const downloaded = r.data?.downloaded ?? data.downloaded ?? job.downloaded ?? 0;
            const total = r.data?.total ?? data.total ?? job.total ?? 0;
            const percent = r.data?.percent ?? data.percent ?? job.percent ?? 0;
            setJobs((prev) =>
              prev.map((j) => {
                if (j.id !== id) return j;
                if (data?.status === "unknown") {
                  return {
                    ...j,
                    ...data,
                    status: "unknown",
                    error: data.error || j.error || "Job not found",
                    downloaded: j.downloaded ?? downloaded,
                    total: j.total ?? total,
                    percent: j.percent ?? percent,
                  };
                }
                return { ...j, ...data, downloaded, total, percent };
              }),
            );
          } catch {
            // ignore transient errors
          }
        }, 1000);
      } else if (!active && hasPoller) {
        clearInterval(pollersRef.current[id]);
        delete pollersRef.current[id];
      }
    });

    return () => {
      Object.values(pollersRef.current).forEach((t) => clearInterval(t));
      pollersRef.current = {};
    };
  }, [jobs]);

  useEffect(() => {
    const anyActive = jobs.some((j) => ["running", "paused"].includes(j.status));
    const anyError = jobs.some((j) => j.status === "error");

    if (!didInitRef.current) {
      didInitRef.current = true;
      // Do not auto-show on initial load (e.g. stale job history).
      setRecentVisible(false);
      return;
    }

    if (anyActive || anyError) bumpRecent();
  }, [jobs]);

  useEffect(() => {
    return () => {
      if (recentTimerRef.current) {
        clearTimeout(recentTimerRef.current);
        recentTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;

    const checkComposer = () => {
      const nextSurface = detectComposerSurface();
      setComposerSurface((prev) => (prev === nextSurface ? prev : nextSurface));
    };

    checkComposer();

    const observer = new MutationObserver(() => checkComposer());
    if (document.body) {
      observer.observe(document.body, { childList: true, subtree: true });
    }
    window.addEventListener("resize", checkComposer);

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", checkComposer);
    };
  }, []);

  const hasComposer = composerSurface !== "none";
  const activeJobs = jobs.filter((j) => ["running", "paused"].includes(j.status));
  const errorJobs = jobs.filter((j) => j.status === "error");
  const anyActive = activeJobs.length > 0;
  const hasCompleted = jobs.some((j) => ["completed", "canceled", "error"].includes(j.status));

  const deriveModelsRoot = (jobPath, modelName) => {
    if (!jobPath || !modelName) return jobPath || undefined;
    const raw = String(jobPath);
    const lower = raw.toLowerCase();
    const needle = String(modelName).toLowerCase();
    const idxSlash = lower.lastIndexOf(`/${needle}`);
    const idxBack = lower.lastIndexOf(`\\${needle}`);
    const idx = Math.max(idxSlash, idxBack);
    if (idx >= 0 && idx + needle.length + 1 === lower.length) {
      return raw.slice(0, idx);
    }
    return raw;
  };

  const restartJob = async (job) => {
    const body = {
      model: job.model,
      ...(job.path ? { path: deriveModelsRoot(job.path, job.model) } : {}),
    };
    const r = await axios.post("/api/models/jobs", body);
    const nextJob = r.data?.job;
    if (!nextJob?.id) throw new Error("Job restart failed");
    const downloaded = r.data?.downloaded ?? 0;
    const total = r.data?.total ?? nextJob.total ?? 0;
    const percent = r.data?.percent ?? 0;
    setJobs((prev) => {
      const filtered = prev.filter((j) => j.id !== job.id);
      const entry = {
        ...job,
        ...nextJob,
        id: nextJob.id,
        status: nextJob.status || "running",
        downloaded,
        total,
        percent,
      };
      return [entry, ...filtered];
    });
  };

  const shouldRender = expanded || anyActive || recentVisible;
  if (!shouldRender) return <div style={{ display: "none" }} aria-hidden="true" />;

  const cancelHoverTimer = () => {
    if (!hoverTimerRef.current) return;
    clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = null;
  };

  const scheduleCollapse = () => {
    cancelHoverTimer();
    if (!expanded) return;
    const sinceOpen = Date.now() - lastExpandedAtRef.current;
    const grace = Math.max(0, OPEN_GRACE_MS - sinceOpen);
    hoverTimerRef.current = setTimeout(() => setExpanded(false), CLOSE_DELAY_MS + grace);
  };

  const collapsedSummary = activeJobs[0] || errorJobs[0] || null;
  const collapsedCount = activeJobs.length + errorJobs.length;

  return (
    <div
      className={`download-tray ${expanded ? "expanded" : "collapsed"}${
        hasComposer ? "" : " no-composer"
      }${composerSurface === "input-box" ? " with-input-box" : ""}${
        composerSurface === "entry-button" ? " with-entry-button" : ""
      }`}
      onMouseEnter={cancelHoverTimer}
      onMouseLeave={scheduleCollapse}
    >
      <div className="download-tray-rail center-rail">
        {!expanded && (anyActive || recentVisible) && (
          <button
            type="button"
            className="download-tray-peek"
            title="Show downloads"
            onClick={() => setExpanded(true)}
          >
            <span className="download-tray-peek-icon">{"\u2B07"}</span>
            {collapsedSummary ? (
              <span className="download-tray-peek-text">
                <span className="download-tray-peek-model">{collapsedSummary.model}</span>
                {typeof collapsedSummary.percent === "number" ? (
                  <span className="download-tray-peek-meta">
                    {Math.round((collapsedSummary.percent || 0) * 100)}%
                    {collapsedCount > 1 ? ` (+${collapsedCount - 1})` : ""}
                  </span>
                ) : collapsedCount > 1 ? (
                  <span className="download-tray-peek-meta">{`(+${collapsedCount - 1})`}</span>
                ) : null}
              </span>
            ) : (
              <span className="download-tray-peek-text">Downloads</span>
            )}
          </button>
        )}

        {expanded && (
          <div className="download-tray-content">
            <div className="download-tray-header">
              <button
                type="button"
                className="download-tray-toggle"
                title="Hide"
                onClick={() => setExpanded(false)}
                onMouseEnter={() => {
                  cancelHoverTimer();
                  hoverTimerRef.current = setTimeout(() => setExpanded(false), CLOSE_DELAY_MS);
                }}
                onMouseLeave={cancelHoverTimer}
              >
                {"\u2212"}
              </button>
              <div className="download-tray-title">Downloads</div>
              <div className="download-tray-header-actions">
                {hasCompleted && (
                  <button
                    type="button"
                    className="dl-btn"
                    title="Clear completed"
                    onClick={() =>
                      setJobs((prev) =>
                        prev.filter((j) => !["completed", "canceled", "error"].includes(j.status)),
                      )
                    }
                  >
                    Clear Completed
                  </button>
                )}
                <button
                  type="button"
                  className="download-tray-close"
                  title="Close"
                  onClick={() => {
                    setExpanded(false);
                    setRecentVisible(false);
                  }}
                  aria-label="Close downloads"
                >
                  {"\u2715"}
                </button>
              </div>
            </div>

            {(jobs.length ? jobs : [{ id: "__empty__", status: "empty" }]).map((job) => {
              if (job.status === "empty") {
                return (
                  <div className="download-item" key="__empty__">
                    <div className="download-item-meta">No active downloads.</div>
                  </div>
                );
              }
              const pct = Math.round((job.percent || 0) * 100);
              const canControl = ["running", "paused", "error", "unknown"].includes(job.status);
              return (
                <div className="download-item" key={job.id}>
                  <div className="download-item-row">
                    <div className="download-item-name">{job.model}</div>
                    {canControl ? (
                      <div className="download-item-actions">
                        {job.status === "running" ? (
                          <button
                            type="button"
                            className="dl-btn"
                            title="Pause"
                            onClick={async () => {
                              try {
                                await axios.post(`/api/models/jobs/${job.id}/pause`);
                                setJobs((prev) =>
                                  prev.map((j) =>
                                    j.id === job.id ? { ...j, status: "paused" } : j,
                                  ),
                                );
                              } catch (e) {
                                const status = e?.response?.status;
                                setJobs((prev) =>
                                  prev.map((j) =>
                                    j.id === job.id
                                      ? {
                                          ...j,
                                          status: status === 404 ? "unknown" : j.status,
                                          error:
                                            e?.response?.data?.detail ||
                                            e?.message ||
                                            "Pause failed",
                                        }
                                      : j,
                                  ),
                                );
                              }
                            }}
                          >
                            {"\u23F8"}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="dl-btn"
                            title={
                              job.status === "unknown"
                                ? "Restart"
                                : job.status === "error"
                                  ? "Retry"
                                  : "Resume"
                            }
                            onClick={async () => {
                              try {
                                if (job.status === "unknown") {
                                  await restartJob(job);
                                  return;
                                }
                                await axios.post(`/api/models/jobs/${job.id}/resume`);
                                setJobs((prev) =>
                                  prev.map((j) =>
                                    j.id === job.id ? { ...j, status: "running" } : j,
                                  ),
                                );
                              } catch (e) {
                                const status = e?.response?.status;
                                if (status === 404) {
                                  try {
                                    await restartJob(job);
                                    return;
                                  } catch {}
                                }
                                setJobs((prev) =>
                                  prev.map((j) =>
                                    j.id === job.id
                                      ? {
                                          ...j,
                                          status: status === 404 ? "unknown" : j.status,
                                          error:
                                            e?.response?.data?.detail ||
                                            e?.message ||
                                            "Resume failed",
                                        }
                                      : j,
                                  ),
                                );
                              }
                            }}
                          >
                            {"\u25B6"}
                          </button>
                        )}
                        <button
                          type="button"
                          className="dl-btn danger"
                          title={job.status === "unknown" ? "Remove" : "Cancel"}
                          onClick={async () => {
                            try {
                              if (job.status === "unknown") {
                                setJobs((prev) => prev.filter((j) => j.id !== job.id));
                                return;
                              }
                              await axios.post(`/api/models/jobs/${job.id}/cancel`);
                              setJobs((prev) =>
                                prev.map((j) =>
                                  j.id === job.id ? { ...j, status: "canceled" } : j,
                                ),
                              );
                            } catch (e) {
                              const status = e?.response?.status;
                              if (status === 404) {
                                setJobs((prev) => prev.filter((j) => j.id !== job.id));
                                return;
                              }
                              setJobs((prev) =>
                                prev.map((j) =>
                                  j.id === job.id
                                    ? {
                                        ...j,
                                        error:
                                          e?.response?.data?.detail ||
                                          e?.message ||
                                          "Cancel failed",
                                      }
                                    : j,
                                ),
                              );
                            }
                          }}
                        >
                          {"\u2715"}
                        </button>
                      </div>
                    ) : null}
                  </div>

                  <div className="download-progress-track small">
                    <div
                      className="download-progress-fill"
                      style={{ width: `${isFinite(pct) ? pct : 0}%` }}
                    />
                  </div>

                  <div className="download-item-meta">
                    {pct}% {"\u2014"} {humanBytes(job.downloaded || 0, job.total || 0)}
                  </div>

                  {job.status === "error" && job.error ? (
                    <div className="status-note warn" style={{ marginTop: 6 }}>
                      {String(job.error)}
                    </div>
                  ) : null}
                  {job.status === "unknown" && job.error ? (
                    <div className="status-note warn" style={{ marginTop: 6 }}>
                      {String(job.error)} (server restart?) — press ▶ to restart.
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default DownloadTray;
