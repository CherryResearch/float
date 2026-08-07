import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { useLocation, useNavigate } from "react-router-dom";
import "../styles/KnowledgeViewer.css";
import CalendarTab from "./CalendarTab";
import MemoryTab from "./MemoryTab";
import ThreadsTab from "./ThreadsTab";
import DocumentsTab from "./DocumentsTab";
import KnowledgeSkillsTab from "./KnowledgeSkillsTab";
import KnowledgeSyncTab from "./KnowledgeSyncTab";
import { Line, Rect } from "./Skeleton";

const KnowledgeVisualizationsTab = React.lazy(() => import("./KnowledgeVisualizationsTab"));

const KnowledgeViewer = () => {
  const [, setMemoryDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState(() => localStorage.getItem("knowledge-tab") || "memory");
  const table = "default";
  const tabsRef = useRef(null);
  const [tabsHeight, setTabsHeight] = useState(0);
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const tabParam = params.get("tab");
  const focusMemoryKey = params.get("key");
  const focusDocId = params.get("id");
  const focusEventId = params.get("event_id");

  const loadDocs = () => {
    setLoading(true);
    axios
      .get(`/api/knowledge/list?table=${table}`)
      .then((res) => {
        const ids = res.data.ids || [];
        const metas = res.data.metadatas || [];
        const list = ids.map((id, idx) => ({ id, metadata: metas[idx] || {} }));
        setMemoryDocs(list);
      })
      .catch((err) => console.error("Failed to load knowledge", err))
      .finally(() => setLoading(false));
  };

  useEffect(loadDocs, [table]);

  useEffect(() => {
    const measure = () => {
      const height = tabsRef.current?.offsetHeight || 0;
      setTabsHeight(height);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const changeTab = (t) => {
    setTab(t);
    localStorage.setItem("knowledge-tab", t);
    const next = new URLSearchParams(location.search);
    next.set("tab", t);
    navigate(
      {
        pathname: location.pathname,
        search: `?${next.toString()}`,
      },
      { replace: true },
    );
  };

  useEffect(() => {
    // Keep UI tab state in sync with direct links and back/forward navigation.
    if (!tabParam || tabParam === tab) return;
    setTab(tabParam);
    localStorage.setItem("knowledge-tab", tabParam);
  }, [tabParam, tab]);

  useEffect(() => {
    const activeTab = tabsRef.current?.querySelector("button.active");
    if (activeTab && typeof activeTab.scrollIntoView === "function") {
      activeTab.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }, [tab]);

  return (
    <div
      className={`knowledge-viewer knowledge-viewer--${tab}`}
      style={{
        "--knowledge-tabs-height": tabsHeight ? `${tabsHeight}px` : undefined,
      }}
    >
      <div className="tabs link-tabs" ref={tabsRef}>
        <button
          className={tab === "memory" ? "active" : ""}
          onClick={() => changeTab("memory")}
          aria-current={tab === "memory" ? "page" : undefined}
        >
          memory
        </button>
        <button
          className={tab === "calendar" ? "active" : ""}
          onClick={() => changeTab("calendar")}
          aria-current={tab === "calendar" ? "page" : undefined}
        >
          calendar
        </button>
        <button
          className={tab === "threads" ? "active" : ""}
          onClick={() => changeTab("threads")}
          aria-current={tab === "threads" ? "page" : undefined}
        >
          threads
        </button>
        <button
          className={tab === "visualizations" ? "active" : ""}
          onClick={() => changeTab("visualizations")}
          aria-current={tab === "visualizations" ? "page" : undefined}
        >
          visualizations
        </button>
        <button
          className={tab === "documents" ? "active" : ""}
          onClick={() => changeTab("documents")}
          aria-current={tab === "documents" ? "page" : undefined}
        >
          documents
        </button>
        <button
          className={tab === "skills" ? "active" : ""}
          onClick={() => changeTab("skills")}
          aria-current={tab === "skills" ? "page" : undefined}
        >
          skills
        </button>
        <button
          className={tab === "sync" ? "active" : ""}
          onClick={() => changeTab("sync")}
          aria-current={tab === "sync" ? "page" : undefined}
        >
          sync
        </button>
      </div>
      {/* Ensure a consistent canvas height across sub-tabs to prevent layout shift */}
      <div className="knowledge-canvas">
        {tab === "memory" ? (
          <MemoryTab focusKey={focusMemoryKey} />
        ) : tab === "calendar" ? (
          <CalendarTab focusEventId={focusEventId} />
        ) : tab === "threads" ? (
          loading ? (
            <div className="threads-panel">
              <Line width="40%" />
              <Rect height={220} />
            </div>
          ) : (
            <div className="threads-panel">
              <ThreadsTab />
            </div>
          )
        ) : tab === "visualizations" ? (
          <React.Suspense
            fallback={
              <div className="knowledge-viz-state knowledge-viz-state--loading" role="status">
                <Line width="36%" />
                <Rect height={360} />
                <span>Loading graph explorer…</span>
              </div>
            }
          >
            <KnowledgeVisualizationsTab />
          </React.Suspense>
        ) : tab === "skills" ? (
          <KnowledgeSkillsTab />
        ) : tab === "sync" ? (
          <KnowledgeSyncTab />
        ) : (
          loading ? (
            <div className="docs-panel">
              <Line width="50%" />
              <Rect height={180} />
            </div>
          ) : (
            <DocumentsTab focusId={focusDocId} />
          )
        )}
      </div>
    </div>
  );
};

export default KnowledgeViewer;
