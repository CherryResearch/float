import React from "react";
import axios from "axios";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import SwapVertIcon from "@mui/icons-material/SwapVert";
import CallSplitIcon from "@mui/icons-material/CallSplit";
import { GlobalContext } from "../main";
import "../styles/Sidebar.css";
import {
  handleUnifiedPress,
  supportsHoverInteractions,
} from "../utils/pointerInteractions";

const EMPTY_GLOBAL_STATE = Object.freeze({});
const NOOP_SET_STATE = () => {};

const generateDefaultName = (timestamp = Date.now()) => {
  const date = new Date(timestamp);
  const pad = (n) => String(n).padStart(2, "0");
  return `New Chat ${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const displayNameFromId = (id) => {
  const match = /^sess-(\d+)$/.exec(id || "");
  if (match) {
    return generateDefaultName(parseInt(match[1], 10));
  }
  return id;
};

export const formatConversationDate = (id) => {
  const normalized = String(id || "").trim().replace(/\\/g, "/");
  if (!normalized) return null;
  const basename = normalized.split("/").filter(Boolean).pop() || normalized;
  const storageKey = basename.toLowerCase().endsWith(".json")
    ? basename.slice(0, -5)
    : basename;
  const match = /^sess-(\d+)$/.exec(storageKey);
  if (!match) return null;
  const timestamp = parseInt(match[1], 10);
  if (Number.isNaN(timestamp)) return null;
  const date = new Date(timestamp);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
};

const threadPalette = ["#8F9BFF", "#FFB347", "#7DD3FC", "#F472B6", "#34D399"];

const getThreadColor = (conversationId) => {
  if (!conversationId) return threadPalette[0];
  const hash = conversationId
    .split("")
    .reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return threadPalette[hash % threadPalette.length];
};

const HISTORY_VIEW_MODES = {
  THREADS: "threads",
  FOLDERS: "folders",
};

const HISTORY_SORT_MODES = {
  UPDATED: "updated",
  CREATED: "created",
  ALPHABETICAL: "alphabetical",
  CUSTOM: "custom",
};

const HISTORY_SORT_MODE_ORDER = [
  HISTORY_SORT_MODES.UPDATED,
  HISTORY_SORT_MODES.CREATED,
  HISTORY_SORT_MODES.ALPHABETICAL,
  HISTORY_SORT_MODES.CUSTOM,
];

const HISTORY_SORT_MODE_LABELS = {
  [HISTORY_SORT_MODES.UPDATED]: "updated",
  [HISTORY_SORT_MODES.CREATED]: "created",
  [HISTORY_SORT_MODES.ALPHABETICAL]: "a-z",
  [HISTORY_SORT_MODES.CUSTOM]: "custom",
};

const ROOT_FOLDER_KEY = "__root__";
const THREAD_GROUP_ROOT_KEY = "__thread_root__";
const DEFAULT_FOLDER_COLORS = [
  "#9B8CFF",
  "#7DD3FC",
  "#F472B6",
  "#34D399",
  "#FBBF24",
  "#FCA5A5",
  "#A78BFA",
];
const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 520;
const SIDEBAR_VIEWPORT_GUTTER = 160;
const SIDEBAR_KEYBOARD_STEP = 20;
const SIDEBAR_KEYBOARD_STEP_FAST = 40;
const HISTORY_SORT_MODE_STORAGE_KEY = "historySortMode";
const HISTORY_THREAD_GROUP_COLLAPSE_STORAGE_KEY = "historyThreadGroupCollapsed";
const HISTORY_CUSTOM_ORDER_STORAGE_KEY = "historyCustomOrder";
const HISTORY_DRAG_SCROLL_EDGE_PX = 60;
const HISTORY_DRAG_SCROLL_STEP_PX = 22;

const ensureString = (value) => (typeof value === "string" ? value : "");

const normalizeConversationKey = (value) => {
  const raw = ensureString(value).trim().replace(/\\/g, "/");
  if (!raw) return "";
  const withoutAnchor = raw.split("#", 1)[0].trim();
  if (!withoutAnchor) return "";
  if (withoutAnchor.toLowerCase().endsWith(".json")) {
    return withoutAnchor.slice(0, -5);
  }
  return withoutAnchor;
};

const extractSessionBasename = (name) => {
  const normalized = ensureString(name);
  if (!normalized) return "";
  const segments = normalized.split("/").filter(Boolean);
  return segments.length ? segments[segments.length - 1] : normalized;
};

const parseSessionTimestamp = (name) => {
  const base = extractSessionBasename(name);
  const match = /^sess-(\d+)$/.exec(base || "");
  if (!match) return null;
  const ts = parseInt(match[1], 10);
  return Number.isFinite(ts) ? ts : null;
};

const safeDateValue = (value) => {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  const ms = date.getTime();
  return Number.isNaN(ms) ? null : ms;
};

const getFolderPathFromName = (name) => {
  const normalized = ensureString(name);
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length <= 1) return "";
  return segments.slice(0, -1).join("/");
};

const splitFolderPath = (path) => {
  if (!path) return [];
  return String(path)
    .split("/")
    .map((segment) => segment.trim())
    .filter(Boolean);
};

const sanitizeFilename = (value) => {
  const base = ensureString(value).trim() || "conversation";
  const cleaned = base.replace(/[<>:"/\\|?*\x00-\x1F]/g, "-").trim();
  const collapsed = cleaned.replace(/\s+/g, "_");
  return collapsed || "conversation";
};

const sanitizeFolderPath = (value) => {
  const raw = ensureString(value).trim().replace(/\\/g, "/");
  if (!raw) return "";
  const segments = raw
    .split("/")
    .map((segment) => segment.replace(/[<>:"/\\|?*\x00-\x1F]/g, "-").trim())
    .filter(Boolean);
  return segments.join("/");
};

const sanitizeBaseName = (value) => {
  const raw = ensureString(value).trim();
  if (!raw) return "";
  return raw.replace(/[<>:"/\\|?*\x00-\x1F]/g, "-").trim();
};

const ensureExtension = (filename, ext) => {
  const trimmed = ensureString(filename).trim();
  if (!trimmed) return `conversation.${ext}`;
  if (trimmed.toLowerCase().endsWith(`.${ext}`)) return trimmed;
  return `${trimmed}.${ext}`;
};

const normalizeExportFormat = (value) => {
  const raw = (value || "").toString().trim().toLowerCase();
  if (raw === "markdown") return "md";
  if (raw === "txt") return "text";
  if (raw === "md" || raw === "json" || raw === "text") return raw;
  return "md";
};

const inferImportFormatFromFilename = (filename) => {
  const name = (filename || "").toString().trim().toLowerCase();
  if (name.endsWith(".zip")) return "zip";
  if (name.endsWith(".json")) return "json";
  if (name.endsWith(".md") || name.endsWith(".markdown")) return "markdown";
  if (name.endsWith(".txt")) return "text";
  return "md";
};

const renderInBodyPortal = (content) => {
  if (typeof document === "undefined" || !document.body) return content;
  return createPortal(content, document.body);
};

export const getHorizontalScrollIndicatorMetrics = ({
  scrollLeft = 0,
  clientWidth = 0,
  scrollWidth = 0,
} = {}) => {
  const viewport = Math.max(0, Number(clientWidth) || 0);
  const content = Math.max(viewport, Number(scrollWidth) || 0);
  const maxScroll = Math.max(0, content - viewport);
  if (!viewport || maxScroll <= 1) {
    return {
      hasOverflow: false,
      thumbWidth: 1,
      thumbOffset: 0,
    };
  }
  const thumbWidth = Math.max(0.18, Math.min(1, viewport / content));
  const progress = Math.min(1, Math.max(0, (Number(scrollLeft) || 0) / maxScroll));
  return {
    hasOverflow: true,
    thumbWidth,
    thumbOffset: (1 - thumbWidth) * progress,
  };
};

const HistorySidebar = ({ collapsed = false, onToggle }) => {
  const globalContext = React.useContext(GlobalContext);
  const state = globalContext?.state || EMPTY_GLOBAL_STATE;
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const navigate = useNavigate();
  const [conversations, setConversations] = React.useState([]);
  const [historyMode, setHistoryMode] = React.useState(HISTORY_VIEW_MODES.FOLDERS);
  const [sortMode, setSortMode] = React.useState(() => {
    if (typeof localStorage === "undefined") return HISTORY_SORT_MODES.UPDATED;
    try {
      const raw = String(localStorage.getItem(HISTORY_SORT_MODE_STORAGE_KEY) || "");
      if (HISTORY_SORT_MODE_ORDER.includes(raw)) return raw;
    } catch {}
    return HISTORY_SORT_MODES.UPDATED;
  });
  const [customOrder, setCustomOrder] = React.useState(() => {
    if (typeof localStorage === "undefined") return {};
    try {
      const raw = localStorage.getItem(HISTORY_CUSTOM_ORDER_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return {};
      const normalized = {};
      Object.entries(parsed).forEach(([key, value]) => {
        const numeric = Number(value);
        if (Number.isFinite(numeric)) {
          normalized[String(key)] = numeric;
        }
      });
      return normalized;
    } catch {
      return {};
    }
  });
  const [collapsedFolders, setCollapsedFolders] = React.useState(() => new Set());
  const [collapsedThreadGroups, setCollapsedThreadGroups] = React.useState(() => {
    if (typeof localStorage === "undefined") return new Set();
    try {
      const raw = localStorage.getItem(HISTORY_THREAD_GROUP_COLLAPSE_STORAGE_KEY);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(parsed.filter((value) => typeof value === "string").map(String));
    } catch {
      return new Set();
    }
  });
  const [activeMenuKey, setActiveMenuKey] = React.useState(null);
  const [folderMenuKey, setFolderMenuKey] = React.useState(null);
  const [folderSettings, setFolderSettings] = React.useState({});
  const [moveTarget, setMoveTarget] = React.useState(null);
  const [moveFolderValue, setMoveFolderValue] = React.useState("");
  const [renameTarget, setRenameTarget] = React.useState(null);
  const [renameValue, setRenameValue] = React.useState("");
  const [renameSuggestBusy, setRenameSuggestBusy] = React.useState(false);
  const [folderRenameTarget, setFolderRenameTarget] = React.useState(null);
  const [folderRenameValue, setFolderRenameValue] = React.useState("");
  const [newFolderParentTarget, setNewFolderParentTarget] = React.useState(null);
  const [newFolderValue, setNewFolderValue] = React.useState("");
  const [deleteFolderTarget, setDeleteFolderTarget] = React.useState(null);
  const [draggingFolderPath, setDraggingFolderPath] = React.useState("");
  const [draggingConversation, setDraggingConversation] = React.useState(null);
  const [dragOverFolder, setDragOverFolder] = React.useState(null);
  const [dragOverConversation, setDragOverConversation] = React.useState(null);
  const [exportTarget, setExportTarget] = React.useState(null);
  const [importStatus, setImportStatus] = React.useState("");
  const [importBusy, setImportBusy] = React.useState(false);
  const [importReview, setImportReview] = React.useState({
    open: false,
    file: null,
    detectedFiles: [],
    selectedFiles: {},
    destinationFolder: "",
  });
  const [exportOptions, setExportOptions] = React.useState({
    format: "md",
    includeChat: true,
    includeThoughts: true,
    includeTools: true,
    filename: "",
  });
  const [exportDefaults, setExportDefaults] = React.useState({
    format: "md",
    includeChat: true,
    includeThoughts: true,
    includeTools: true,
  });
  const [threadMap, setThreadMap] = React.useState({});
  const [activeThreadTag, setActiveThreadTag] = React.useState(null);
  const [activeThreadFilter, setActiveThreadFilter] = React.useState(null);
  const [isResizing, setIsResizing] = React.useState(false);
  const [historyControlsIndicator, setHistoryControlsIndicator] = React.useState({
    hasOverflow: false,
    thumbWidth: 1,
    thumbOffset: 0,
  });
  const ephemeralSortRef = React.useRef(new Map());
  const importFileInputRef = React.useRef(null);
  const historyControlsScrollRef = React.useRef(null);
  const historyControlsContentRef = React.useRef(null);
  const apiUnavailable = state.backendMode === "api" && state.apiStatus !== "online";
  const sidebarRef = React.useRef(null);
  const hasMountedSessionFetchRef = React.useRef(false);
  const historyBodyRef = React.useRef(null);

  const getThreadTagsForConversation = React.useCallback(
    (conversationKey) => {
      const normalized = normalizeConversationKey(conversationKey);
      if (!normalized) return [];
      const direct = threadMap[normalized];
      if (Array.isArray(direct) && direct.length) return direct;
      const basename = extractSessionBasename(normalized);
      const fallback = basename ? threadMap[basename] : null;
      if (Array.isArray(fallback) && fallback.length) return fallback;
      return [];
    },
    [threadMap],
  );

  const resolveThreadGroupId = React.useCallback((conversation) => {
    const explicit = ensureString(conversation?.threadId).trim();
    if (explicit && explicit.toLowerCase() !== "ungrouped") {
      return explicit;
    }
    if (Array.isArray(conversation?.threadTags)) {
      const firstTag = ensureString(conversation.threadTags[0]).trim();
      if (firstTag) return firstTag;
    }
    return "ungrouped";
  }, []);

  const clampSidebarWidth = React.useCallback((value, minWidth, maxWidth) => {
    if (!Number.isFinite(value)) return minWidth;
    return Math.min(maxWidth, Math.max(minWidth, value));
  }, []);

  const getSidebarBounds = React.useCallback(() => {
    const minWidth = SIDEBAR_MIN_WIDTH;
    const maxWidth = Math.max(
      minWidth,
      Math.min(SIDEBAR_MAX_WIDTH, window.innerWidth - SIDEBAR_VIEWPORT_GUTTER),
    );
    return { minWidth, maxWidth };
  }, []);

  const applySidebarWidth = React.useCallback(
    (width) => {
      const root = typeof document !== "undefined" ? document.documentElement : null;
      if (!root) return;
      root.style.setProperty("--sidebar-width-left", `${width}px`);
      try {
        localStorage.setItem("sidebarWidthLeft", String(width));
      } catch {}
    },
    [],
  );

  const resetSidebarWidth = React.useCallback(() => {
    const root = typeof document !== "undefined" ? document.documentElement : null;
    if (!root) return;
    root.style.removeProperty("--sidebar-width-left");
    try {
      localStorage.removeItem("sidebarWidthLeft");
    } catch {}
  }, []);

  const nudgeSidebarWidth = React.useCallback(
    (delta) => {
      const sidebar = sidebarRef.current;
      const { minWidth, maxWidth } = getSidebarBounds();
      const currentWidth = sidebar?.getBoundingClientRect().width || minWidth;
      const next = clampSidebarWidth(currentWidth + delta, minWidth, maxWidth);
      applySidebarWidth(next);
    },
    [applySidebarWidth, clampSidebarWidth, getSidebarBounds],
  );

  const handleResizeKeyDown = React.useCallback(
    (event) => {
      if (collapsed) return;
      if (event.key === "Home") {
        event.preventDefault();
        resetSidebarWidth();
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey ? SIDEBAR_KEYBOARD_STEP_FAST : SIDEBAR_KEYBOARD_STEP;
      const delta = event.key === "ArrowRight" ? step : -step;
      nudgeSidebarWidth(delta);
    },
    [collapsed, nudgeSidebarWidth, resetSidebarWidth],
  );

  const startResize = React.useCallback(
    (event) => {
      if (collapsed) return;
      if (event.button !== 0 && event.pointerType !== "touch") return;
      event.preventDefault();
      const root = document.documentElement;
      const sidebar = sidebarRef.current;
      const startX = event.clientX;
      const startWidth = sidebar?.getBoundingClientRect().width || 0;
      const { minWidth, maxWidth } = getSidebarBounds();
      root.style.setProperty("cursor", "col-resize");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      setIsResizing(true);

      const onMove = (moveEvent) => {
        const delta = moveEvent.clientX - startX;
        const next = clampSidebarWidth(startWidth + delta, minWidth, maxWidth);
        applySidebarWidth(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        root.style.removeProperty("cursor");
        setIsResizing(false);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [applySidebarWidth, clampSidebarWidth, collapsed, getSidebarBounds],
  );

  const syncSessionDisplayName = React.useCallback(
    (rawList) => {
      if (!state.sessionId) return;
      let detectedName = null;
      (rawList || []).forEach((entry) => {
        if (detectedName || !entry) return;
        if (typeof entry === "string") {
          const base = extractSessionBasename(entry);
          const key = base || entry.trim();
          if (key === state.sessionId) {
            detectedName = displayNameFromId(base || entry);
          }
          return;
        }
        if (typeof entry === "object") {
          const key =
            ensureString(entry.name) ||
            ensureString(entry.id) ||
            ensureString(entry.path) ||
            ensureString(entry.slug);
          if (key === state.sessionId) {
            detectedName =
              ensureString(entry.display_name) ||
              ensureString(entry.displayName) ||
              ensureString(entry.title) ||
              displayNameFromId(key);
          }
        }
      });
      if (detectedName && detectedName !== state.sessionName) {
        setState((prev) => ({ ...prev, sessionName: detectedName }));
      }
    },
    [setState, state.sessionId, state.sessionName],
  );

  const fetchConversations = React.useCallback(async () => {
    if (state.backendMode === "api" && state.apiStatus !== "online") {
      return [];
    }
    try {
      const res = await axios.get("/api/conversations", { params: { detailed: true } });
      const list = Array.isArray(res?.data?.conversations) ? res.data.conversations : [];
      syncSessionDisplayName(list);
      setConversations(list);
      return list;
    } catch (err) {
      console.error("Failed to load detailed conversations", err);
      try {
        const res = await axios.get("/api/conversations");
        const raw = Array.isArray(res?.data?.conversations) ? res.data.conversations : [];
        const fallbackList = raw.map((entry) => {
          if (typeof entry === "string") {
            const base = extractSessionBasename(entry) || entry;
            return {
              name: entry,
              display_name: displayNameFromId(base),
            };
          }
          return entry;
        });
        syncSessionDisplayName(fallbackList);
        setConversations(fallbackList);
        return fallbackList;
      } catch (fallbackErr) {
        console.error("Failed to load fallback conversations", fallbackErr);
        setConversations([]);
        return [];
      }
    }
  }, [setConversations, state.apiStatus, state.backendMode, syncSessionDisplayName]);

  React.useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  React.useEffect(() => {
    if (!hasMountedSessionFetchRef.current) {
      hasMountedSessionFetchRef.current = true;
      return;
    }
    fetchConversations();
  }, [state.sessionId, fetchConversations]);

  React.useEffect(() => {
    if (state.backendMode === "api" && state.apiStatus !== "online") return;
    axios
      .get("/api/user-settings")
      .then((res) => {
        const s = res.data || {};
        const nextDefaults = {
          format: normalizeExportFormat(s.export_default_format),
          includeChat:
            typeof s.export_default_include_chat === "boolean"
              ? s.export_default_include_chat
              : true,
          includeThoughts:
            typeof s.export_default_include_thoughts === "boolean"
              ? s.export_default_include_thoughts
              : true,
          includeTools:
            typeof s.export_default_include_tools === "boolean"
              ? s.export_default_include_tools
              : true,
        };
        setExportDefaults(nextDefaults);
        setExportOptions((prev) => ({
          ...prev,
          format: nextDefaults.format,
          includeChat: nextDefaults.includeChat,
          includeThoughts: nextDefaults.includeThoughts,
          includeTools: nextDefaults.includeTools,
        }));
        if (s.conversation_folders && typeof s.conversation_folders === "object") {
          setFolderSettings(s.conversation_folders);
        }
      })
      .catch(() => {});
  }, [state.apiStatus, state.backendMode]);

  React.useEffect(() => {
    if (state.backendMode === "api" && state.apiStatus !== "online") return;
    axios
      .get("/api/threads/summary")
      .then((res) => {
        const summary = res.data?.summary || {};
        const threads = summary?.threads || {};
        const map = {};
        const appendThreadTag = (key, tag) => {
          const cleanKey = normalizeConversationKey(key);
          const cleanTag = ensureString(tag).trim();
          if (!cleanKey || !cleanTag) return;
          if (!map[cleanKey]) map[cleanKey] = [];
          if (!map[cleanKey].includes(cleanTag)) {
            map[cleanKey].push(cleanTag);
          }
          const base = extractSessionBasename(cleanKey);
          if (base && base !== cleanKey) {
            if (!map[base]) map[base] = [];
            if (!map[base].includes(cleanTag)) {
              map[base].push(cleanTag);
            }
          }
        };
        Object.entries(threads).forEach(([tname, items]) => {
          if (!Array.isArray(items)) return;
          items.forEach((it) => {
            appendThreadTag(it?.conversation, tname);
          });
        });
        setThreadMap(map);
      })
      .catch(() => {});
  }, [state.apiStatus, state.backendMode]);

  React.useEffect(() => {
    if (!activeMenuKey && !folderMenuKey) return undefined;
    const handleClick = (event) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        setActiveMenuKey(null);
        setFolderMenuKey(null);
        return;
      }
      if (target.closest(".conv-menu-wrapper")) return;
      if (target.closest(".folder-menu-wrapper")) return;
      setActiveMenuKey(null);
      setFolderMenuKey(null);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [activeMenuKey, folderMenuKey]);

  const loadConversation = async (id) => {
    try {
      const res = await axios.get(`/api/conversations/${encodeURIComponent(id)}`);
      const loadedMessages = res.data.messages || [];
      if (typeof sessionStorage !== "undefined") {
        try {
          sessionStorage.setItem(
            `float:conv-loaded:${id}`,
            JSON.stringify(loadedMessages),
          );
        } catch {}
      }
      setState((prev) => ({
        ...prev,
        conversation: loadedMessages,
        sessionId: id,
        sessionName: displayNameFromId(id),
      }));
      navigate("/");
    } catch (err) {
      console.error("Failed to load conversation", err);
    }
  };

  const newChat = () => {
    const newId = `sess-${Date.now()}`;
    setState((prev) => ({
      ...prev,
      conversation: [],
      sessionId: newId,
      sessionName: displayNameFromId(newId),
    }));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("float:new-chat"));
    }
  };

  const forkConversation = async () => {
    const newId = `sess-${Date.now()}`;
    try {
      await axios.post(`/api/context/${state.sessionId}/branch`, { new_id: newId });
      setState((prev) => ({
        ...prev,
        sessionId: newId,
        sessionName: displayNameFromId(newId),
      }));
    } catch (err) {
      console.error("Fork failed", err);
    }
  };

  React.useEffect(() => {
    try {
      localStorage.setItem(HISTORY_SORT_MODE_STORAGE_KEY, sortMode);
    } catch {}
  }, [sortMode]);

  const syncHistoryControlsIndicator = React.useCallback(() => {
    const next = getHorizontalScrollIndicatorMetrics({
      scrollLeft: historyControlsScrollRef.current?.scrollLeft,
      clientWidth: historyControlsScrollRef.current?.clientWidth,
      scrollWidth: historyControlsScrollRef.current?.scrollWidth,
    });
    setHistoryControlsIndicator((prev) => (
      prev.hasOverflow === next.hasOverflow
      && Math.abs(prev.thumbWidth - next.thumbWidth) < 0.001
      && Math.abs(prev.thumbOffset - next.thumbOffset) < 0.001
        ? prev
        : next
    ));
  }, []);

  React.useEffect(() => {
    const scrollNode = historyControlsScrollRef.current;
    if (!scrollNode) return undefined;
    const handleSync = () => syncHistoryControlsIndicator();
    handleSync();
    scrollNode.addEventListener("scroll", handleSync, { passive: true });
    if (typeof window !== "undefined") {
      window.addEventListener("resize", handleSync);
    }
    let resizeObserver = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(handleSync);
      resizeObserver.observe(scrollNode);
      if (historyControlsContentRef.current) {
        resizeObserver.observe(historyControlsContentRef.current);
      }
    }
    return () => {
      scrollNode.removeEventListener("scroll", handleSync);
      if (typeof window !== "undefined") {
        window.removeEventListener("resize", handleSync);
      }
      resizeObserver?.disconnect();
    };
  }, [syncHistoryControlsIndicator]);

  React.useEffect(() => {
    try {
      localStorage.setItem(
        HISTORY_CUSTOM_ORDER_STORAGE_KEY,
        JSON.stringify(customOrder || {}),
      );
    } catch {}
  }, [customOrder]);

  React.useEffect(() => {
    try {
      localStorage.setItem(
        HISTORY_THREAD_GROUP_COLLAPSE_STORAGE_KEY,
        JSON.stringify(Array.from(collapsedThreadGroups || [])),
      );
    } catch {}
  }, [collapsedThreadGroups]);

  const compareConversations = React.useCallback(
    (a, b) => {
      if (sortMode === HISTORY_SORT_MODES.ALPHABETICAL) {
        return String(a.displayName || "").localeCompare(String(b.displayName || ""), undefined, {
          sensitivity: "base",
          numeric: true,
        });
      }
      if (sortMode === HISTORY_SORT_MODES.CUSTOM) {
        const aRank = Number(customOrder?.[a.storageKey]);
        const bRank = Number(customOrder?.[b.storageKey]);
        const aHas = Number.isFinite(aRank);
        const bHas = Number.isFinite(bRank);
        if (aHas && bHas && aRank !== bRank) return aRank - bRank;
        if (aHas !== bHas) return aHas ? -1 : 1;
        return (b.updatedSortKey || 0) - (a.updatedSortKey || 0);
      }
      if (sortMode === HISTORY_SORT_MODES.CREATED) {
        return (b.createdSortKey || 0) - (a.createdSortKey || 0);
      }
      return (b.updatedSortKey || 0) - (a.updatedSortKey || 0);
    },
    [customOrder, sortMode],
  );

  const compareFolderNodes = React.useCallback(
    (a, b) => {
      if (
        sortMode === HISTORY_SORT_MODES.ALPHABETICAL ||
        sortMode === HISTORY_SORT_MODES.CUSTOM
      ) {
        return String(a.name || "").localeCompare(String(b.name || ""), undefined, {
          sensitivity: "base",
          numeric: true,
        });
      }
      return (b.sortKey || 0) - (a.sortKey || 0) || a.name.localeCompare(b.name);
    },
    [sortMode],
  );

  const normalizedConversations = React.useMemo(() => {
    const entries = [];
    const seen = new Set();
    (conversations || []).forEach((entry) => {
      if (!entry) return;
      if (typeof entry === "string") {
        const trimmed = entry.trim();
        if (!trimmed) return;
        const folderPath = getFolderPathFromName(trimmed);
        const base = extractSessionBasename(trimmed);
        const timestamp = parseSessionTimestamp(trimmed);
        const createdAt = timestamp || null;
        const updatedAt = timestamp || null;
        const createdSortKey = createdAt || updatedAt || 0;
        const updatedSortKey = updatedAt || createdAt || 0;
        entries.push({
          storageKey: trimmed,
          displayName: displayNameFromId(base),
          folderPath,
          threadId: null,
          threadTags: getThreadTagsForConversation(trimmed),
          createdSortKey,
          updatedSortKey,
          createdAt,
          updatedAt,
          id: null,
          messageCount: null,
        });
        seen.add(trimmed);
        return;
      }
      if (typeof entry === "object") {
        const storageKey = ensureString(entry.name || entry.id || entry.path || entry.slug);
        if (!storageKey) return;
        const folderPath =
          ensureString(entry.folder_path) ||
          ensureString(entry.folder) ||
          getFolderPathFromName(ensureString(entry.path)) ||
          getFolderPathFromName(storageKey);
        const displaySource =
          ensureString(entry.display_name) ||
          ensureString(entry.title) ||
          ensureString(entry.label) ||
          ensureString(entry.name) ||
          storageKey;
        const createdAt = safeDateValue(
          entry.created_at || entry.createdAt || entry.started_at || entry.startedAt,
        );
        const updatedAt = safeDateValue(
          entry.updated_at || entry.updatedAt || entry.modified_at || entry.modifiedAt,
        );
        const threadId = ensureString(entry.thread_id || entry.threadId);
        const mapTags = getThreadTagsForConversation(storageKey);
        const rawTags =
          mapTags.length
            ? mapTags
            : (Array.isArray(entry.thread_tags) && entry.thread_tags) ||
              (Array.isArray(entry.threadTags) && entry.threadTags) ||
              (Array.isArray(entry.threads) && entry.threads) ||
              (Array.isArray(entry.tags) && entry.tags) ||
              (threadId ? [threadId] : []);
        const threadTags = rawTags
          .map((tag) => (typeof tag === "string" ? tag.trim() : String(tag || "")))
          .filter(Boolean);
        const normalizedThreadId =
          threadId
          || (threadTags.length ? threadTags[0] : "");
        const fallbackSort = parseSessionTimestamp(storageKey) || 0;
        const createdSortKey = createdAt || updatedAt || fallbackSort;
        const updatedSortKey = updatedAt || createdAt || fallbackSort;
        entries.push({
          storageKey,
          displayName: displaySource || displayNameFromId(storageKey),
          folderPath,
          threadId: normalizedThreadId || null,
          threadTags,
          createdSortKey,
          updatedSortKey,
          createdAt,
          updatedAt,
          id: ensureString(entry.id),
          messageCount: entry.message_count ?? null,
        });
        seen.add(storageKey);
      }
    });
    const currentSessionId = ensureString(state.sessionId);
    if (currentSessionId && !seen.has(currentSessionId)) {
      let baseTimestamp = parseSessionTimestamp(currentSessionId);
      if (!baseTimestamp) {
        if (!ephemeralSortRef.current.has(currentSessionId)) {
          ephemeralSortRef.current.set(currentSessionId, Date.now());
        }
        baseTimestamp = ephemeralSortRef.current.get(currentSessionId);
      }
      const createdAt = baseTimestamp || null;
      const updatedAt = baseTimestamp || null;
      const createdSortKey = createdAt || updatedAt || 0;
      const updatedSortKey = updatedAt || createdAt || 0;
      entries.push({
        storageKey: currentSessionId,
        displayName:
          ensureString(state.sessionName) || displayNameFromId(currentSessionId),
        folderPath: getFolderPathFromName(currentSessionId),
        threadId: null,
        threadTags: getThreadTagsForConversation(currentSessionId),
        createdSortKey: createdSortKey || 0,
        updatedSortKey: updatedSortKey || 0,
        createdAt,
        updatedAt,
        id: null,
        messageCount: null,
      });
    }
    entries.sort(compareConversations);
    return entries;
  }, [
    compareConversations,
    conversations,
    getThreadTagsForConversation,
    state.sessionId,
    state.sessionName,
  ]);

  const folderOptions = React.useMemo(() => {
    const set = new Set();
    normalizedConversations.forEach((conv) => {
      if (conv.folderPath) set.add(conv.folderPath);
    });
    Object.keys(folderSettings || {}).forEach((path) => {
      const clean = sanitizeFolderPath(path);
      if (clean) set.add(clean);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [folderSettings, normalizedConversations]);

  React.useEffect(() => {
    if (!activeMenuKey) {
      setActiveThreadTag(null);
      return;
    }
    const match = normalizedConversations.find(
      (conv) => conv.storageKey === activeMenuKey,
    );
    const next = match?.threadTags?.[0] || null;
    setActiveThreadTag(next);
  }, [activeMenuKey, normalizedConversations]);

  const folderTree = React.useMemo(() => {
    const createNode = (name, path) => ({
      name,
      path,
      sortKey: 0,
      conversations: [],
      children: new Map(),
    });
    const ensureFolder = (root, folderPath) => {
      const segments = splitFolderPath(folderPath);
      if (!segments.length) return root;
      let node = root;
      segments.forEach((segment) => {
        const childPath = node.path ? `${node.path}/${segment}` : segment;
        if (!node.children.has(segment)) {
          node.children.set(segment, createNode(segment, childPath));
        }
        node = node.children.get(segment);
      });
      return node;
    };
    const root = createNode("", "");
    normalizedConversations.forEach((conv) => {
      const segments = splitFolderPath(conv.folderPath);
      if (!segments.length) {
        root.conversations.push(conv);
        root.sortKey = Math.max(root.sortKey, conv.updatedSortKey || 0);
        return;
      }
      let node = root;
      segments.forEach((segment) => {
        const childPath = node.path ? `${node.path}/${segment}` : segment;
        if (!node.children.has(segment)) {
          node.children.set(segment, createNode(segment, childPath));
        }
        node = node.children.get(segment);
        node.sortKey = Math.max(node.sortKey, conv.updatedSortKey || 0);
      });
      node.conversations.push(conv);
    });
    Object.keys(folderSettings || {}).forEach((folderPath) => {
      const clean = sanitizeFolderPath(folderPath);
      if (!clean) return;
      ensureFolder(root, clean);
    });

    const finalize = (node) => {
      node.conversations.sort(compareConversations);
      const children = Array.from(node.children.values());
      children.forEach(finalize);
      const childMax = children.reduce((max, child) => Math.max(max, child.sortKey || 0), 0);
      node.sortKey = Math.max(node.sortKey || 0, childMax);
      node.childList = children.sort(compareFolderNodes);
      return node;
    };

    return finalize(root);
  }, [compareConversations, compareFolderNodes, folderSettings, normalizedConversations]);

  const threadGroups = React.useMemo(() => {
    const groups = new Map();
    normalizedConversations.forEach((conv) => {
      const key = resolveThreadGroupId(conv);
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key).push(conv);
    });
    return Array.from(groups.entries())
      .map(([key, items]) => {
        const sorted = [...items].sort(compareConversations);
        return {
          id: key,
          label: key === "ungrouped" ? "Ungrouped" : key,
          color: getThreadColor(key === "ungrouped" ? sorted[0]?.storageKey || key : key),
          conversations: sorted,
          sortKey: sorted[0]?.updatedSortKey || 0,
        };
      })
      .sort((a, b) => {
        if (
          sortMode === HISTORY_SORT_MODES.ALPHABETICAL ||
          sortMode === HISTORY_SORT_MODES.CUSTOM
        ) {
          return a.label.localeCompare(b.label);
        }
        return (b.sortKey || 0) - (a.sortKey || 0);
      });
  }, [compareConversations, normalizedConversations, resolveThreadGroupId, sortMode]);

  const visibleConversations = React.useMemo(() => {
    if (!activeThreadFilter) return normalizedConversations;
    return normalizedConversations.filter((conv) =>
      (conv.threadTags || []).includes(activeThreadFilter),
    );
  }, [normalizedConversations, activeThreadFilter]);

  const visibleFolderTree = React.useMemo(() => {
    if (!activeThreadFilter) return folderTree;
    const createNode = (name, path) => ({
      name,
      path,
      sortKey: 0,
      conversations: [],
      children: new Map(),
    });
    const ensureFolder = (root, folderPath) => {
      const segments = splitFolderPath(folderPath);
      if (!segments.length) return root;
      let node = root;
      segments.forEach((segment) => {
        const childPath = node.path ? `${node.path}/${segment}` : segment;
        if (!node.children.has(segment)) {
          node.children.set(segment, createNode(segment, childPath));
        }
        node = node.children.get(segment);
      });
      return node;
    };
    const root = createNode("", "");
    visibleConversations.forEach((conv) => {
      const segments = splitFolderPath(conv.folderPath);
      if (!segments.length) {
        root.conversations.push(conv);
        root.sortKey = Math.max(root.sortKey, conv.updatedSortKey || 0);
        return;
      }
      let node = root;
      segments.forEach((segment) => {
        const childPath = node.path ? `${node.path}/${segment}` : segment;
        if (!node.children.has(segment)) {
          node.children.set(segment, createNode(segment, childPath));
        }
        node = node.children.get(segment);
        node.sortKey = Math.max(node.sortKey, conv.updatedSortKey || 0);
      });
      node.conversations.push(conv);
    });
    Object.keys(folderSettings || {}).forEach((folderPath) => {
      const clean = sanitizeFolderPath(folderPath);
      if (!clean) return;
      ensureFolder(root, clean);
    });
    const finalize = (node) => {
      node.conversations.sort(compareConversations);
      const children = Array.from(node.children.values());
      children.forEach(finalize);
      const childMax = children.reduce(
        (max, child) => Math.max(max, child.sortKey || 0),
        0,
      );
      node.sortKey = Math.max(node.sortKey || 0, childMax);
      node.childList = children.sort(compareFolderNodes);
      return node;
    };
    return finalize(root);
  }, [
    activeThreadFilter,
    compareConversations,
    compareFolderNodes,
    folderSettings,
    folderTree,
    visibleConversations,
  ]);

  const visibleThreadGroups = React.useMemo(() => {
    if (!activeThreadFilter) return threadGroups;
    const groups = new Map();
    visibleConversations.forEach((conv) => {
      const key = resolveThreadGroupId(conv);
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key).push(conv);
    });
    return Array.from(groups.entries())
      .map(([key, items]) => {
        const sorted = [...items].sort(compareConversations);
        return {
          id: key,
          label: key === "ungrouped" ? "Ungrouped" : key,
          color: getThreadColor(key === "ungrouped" ? sorted[0]?.storageKey || key : key),
          conversations: sorted,
          sortKey: sorted[0]?.updatedSortKey || 0,
        };
      })
      .sort((a, b) => {
        if (
          sortMode === HISTORY_SORT_MODES.ALPHABETICAL ||
          sortMode === HISTORY_SORT_MODES.CUSTOM
        ) {
          return a.label.localeCompare(b.label);
        }
        return (b.sortKey || 0) - (a.sortKey || 0);
      });
  }, [
    activeThreadFilter,
    compareConversations,
    resolveThreadGroupId,
    sortMode,
    threadGroups,
    visibleConversations,
  ]);

  React.useEffect(() => {
    const available = new Set(normalizedConversations.map((conv) => conv.storageKey));
    setCustomOrder((prev) => {
      const next = { ...(prev || {}) };
      let changed = false;
      let maxRank = -1;
      Object.values(next).forEach((value) => {
        const numeric = Number(value);
        if (Number.isFinite(numeric)) {
          maxRank = Math.max(maxRank, numeric);
        }
      });
      Object.keys(next).forEach((key) => {
        if (!available.has(key)) {
          delete next[key];
          changed = true;
        }
      });
      normalizedConversations.forEach((conv) => {
        const rank = Number(next[conv.storageKey]);
        if (!Number.isFinite(rank)) {
          maxRank += 1;
          next[conv.storageKey] = maxRank;
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [normalizedConversations]);

  const getFolderMeta = React.useCallback(
    (path) => {
      if (!path) return {};
      const meta = folderSettings && typeof folderSettings === "object"
        ? folderSettings[path]
        : null;
      return meta && typeof meta === "object" ? meta : {};
    },
    [folderSettings],
  );

  const getFolderLabel = React.useCallback(
    (path, fallback) => {
      const meta = getFolderMeta(path);
      return ensureString(meta.label) || fallback;
    },
    [getFolderMeta],
  );

  const getFolderDisplayLabel = React.useCallback(
    (path, fallback = "") => {
      const normalized = sanitizeFolderPath(path);
      const rawLabel = getFolderLabel(normalized, fallback);
      if (!rawLabel) return "";
      return rawLabel.endsWith("/") ? rawLabel : `${rawLabel}/`;
    },
    [getFolderLabel],
  );

  const getFolderColor = React.useCallback(
    (path) => {
      const meta = getFolderMeta(path);
      return ensureString(meta.color);
    },
    [getFolderMeta],
  );

  const toggleFolder = React.useCallback((path) => {
    const key = path || ROOT_FOLDER_KEY;
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const toggleThreadGroup = React.useCallback((groupId) => {
    const key = String(groupId || "ungrouped");
    setCollapsedThreadGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const formatDateLabel = React.useCallback((timestamp, fallbackKey = "") => {
    if (timestamp) {
      const date = new Date(timestamp);
      if (!Number.isNaN(date.getTime())) {
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        const hours = String(date.getHours()).padStart(2, "0");
        const minutes = String(date.getMinutes()).padStart(2, "0");
        return `${month}-${day} ${hours}:${minutes}`;
      }
    }
    const fromId = formatConversationDate(fallbackKey);
    return fromId || "--";
  }, []);

  const toggleSortMode = React.useCallback(() => {
    setSortMode((prev) => {
      const idx = HISTORY_SORT_MODE_ORDER.indexOf(prev);
      const nextIdx = idx === -1 ? 0 : (idx + 1) % HISTORY_SORT_MODE_ORDER.length;
      return HISTORY_SORT_MODE_ORDER[nextIdx];
    });
  }, []);

  const saveFolderSettings = React.useCallback(
    async (next) => {
      setFolderSettings(next);
      if (apiUnavailable) return;
      try {
        await axios.post("/api/user-settings", { conversation_folders: next });
      } catch (err) {
        console.error("Failed to save folder settings", err);
      }
    },
    [apiUnavailable],
  );

  const updateFolderMeta = React.useCallback(
    (path, updates) => {
      if (!path) return;
      const current = getFolderMeta(path);
      const nextMeta = { ...current, ...updates };
      Object.keys(nextMeta).forEach((key) => {
        if (nextMeta[key] === "" || nextMeta[key] === null) {
          delete nextMeta[key];
        }
      });
      const next = { ...(folderSettings || {}) };
      if (Object.keys(nextMeta).length) {
        next[path] = nextMeta;
      } else {
        delete next[path];
      }
      saveFolderSettings(next);
    },
    [folderSettings, getFolderMeta, saveFolderSettings],
  );

  const openRenameModal = (conv) => {
    if (!conv || apiUnavailable) return;
    setActiveMenuKey(null);
    setRenameTarget(conv);
    const base = extractSessionBasename(conv.storageKey) || conv.displayName || "";
    setRenameValue(base);
  };

  const openMoveModal = (conv) => {
    if (!conv || apiUnavailable) return;
    setActiveMenuKey(null);
    setMoveTarget(conv);
    setMoveFolderValue(getFolderPathFromName(conv.storageKey));
  };

  const openFolderRenameModal = (path) => {
    if (!path || apiUnavailable) return;
    setFolderMenuKey(null);
    const segments = splitFolderPath(path);
    const base = segments[segments.length - 1] || path;
    setFolderRenameTarget(path);
    setFolderRenameValue(base);
  };

  const openNewFolderModal = (parentPath = "") => {
    if (apiUnavailable) return;
    setFolderMenuKey(null);
    setNewFolderParentTarget(sanitizeFolderPath(parentPath || ""));
    setNewFolderValue("");
  };

  const openDeleteFolderModal = (path) => {
    if (!path || apiUnavailable) return;
    setFolderMenuKey(null);
    setDeleteFolderTarget(path);
  };

  const closeRenameModal = () => {
    setRenameTarget(null);
    setRenameValue("");
    setRenameSuggestBusy(false);
  };

  const closeMoveModal = () => {
    setMoveTarget(null);
    setMoveFolderValue("");
  };

  const closeFolderRenameModal = () => {
    setFolderRenameTarget(null);
    setFolderRenameValue("");
  };

  const closeNewFolderModal = () => {
    setNewFolderParentTarget(null);
    setNewFolderValue("");
  };

  const closeDeleteFolderModal = () => {
    setDeleteFolderTarget(null);
  };

  const suggestRenameValue = async () => {
    if (!renameTarget || apiUnavailable) return;
    try {
      setRenameSuggestBusy(true);
      const res = await axios.get(
        `/api/conversations/${encodeURIComponent(renameTarget.storageKey)}/suggest-name`,
      );
      const suggested = String(res.data?.suggested_name || "").trim();
      if (suggested) setRenameValue(suggested);
    } catch (err) {
      console.error("Suggest name failed", err);
    } finally {
      setRenameSuggestBusy(false);
    }
  };

  const renameConversationStorage = async (conv, nextBaseName) => {
    if (!conv || apiUnavailable) return;
    const baseName = sanitizeBaseName(nextBaseName);
    if (!baseName) return;
    const folderPath = getFolderPathFromName(conv.storageKey);
    const newName = folderPath ? `${folderPath}/${baseName}` : baseName;
    if (!newName || newName === conv.storageKey) return;
    try {
      await axios.post(`/api/conversations/${encodeURIComponent(conv.storageKey)}/rename`, {
        new_name: newName,
      });
      setCustomOrder((prev) => {
        const current = Number(prev?.[conv.storageKey]);
        if (!Number.isFinite(current)) return prev;
        const next = { ...(prev || {}) };
        delete next[conv.storageKey];
        next[newName] = current;
        return next;
      });
      await fetchConversations();
      if (state.sessionId === conv.storageKey) {
        setState((prev) => ({
          ...prev,
          sessionId: newName,
          sessionName: displayNameFromId(newName),
        }));
        await loadConversation(newName);
      }
    } catch (err) {
      console.error("Rename failed", err);
    }
  };

  const moveConversationToFolder = async (conv, folderPath) => {
    if (!conv || apiUnavailable) return;
    const baseName = extractSessionBasename(conv.storageKey);
    if (!baseName) return;
    const nextFolder = sanitizeFolderPath(folderPath || "");
    const newName = nextFolder ? `${nextFolder}/${baseName}` : baseName;
    if (!newName || newName === conv.storageKey) return;
    try {
      await axios.post(`/api/conversations/${encodeURIComponent(conv.storageKey)}/rename`, {
        new_name: newName,
      });
      setCustomOrder((prev) => {
        const current = Number(prev?.[conv.storageKey]);
        if (!Number.isFinite(current)) return prev;
        const next = { ...(prev || {}) };
        delete next[conv.storageKey];
        next[newName] = current;
        return next;
      });
      await fetchConversations();
      if (state.sessionId === conv.storageKey) {
        setState((prev) => ({
          ...prev,
          sessionId: newName,
          sessionName: displayNameFromId(newName),
        }));
        await loadConversation(newName);
      }
    } catch (err) {
      console.error("Move failed", err);
    }
  };

  const renameFolderPath = async (oldPath, newPath) => {
    if (apiUnavailable) return;
    if (!oldPath || !newPath || oldPath === newPath) return;
    const oldPrefix = `${oldPath}/`;
    const newPrefix = `${newPath}/`;
    const targets = normalizedConversations.filter(
      (conv) => conv.folderPath === oldPath || conv.folderPath.startsWith(oldPrefix),
    );
    const renameMap = {};
    let activeRename = null;
    for (const conv of targets) {
      const base = extractSessionBasename(conv.storageKey);
      let newName = "";
      if (conv.folderPath === oldPath) {
        newName = `${newPath}/${base}`;
      } else {
        newName = conv.storageKey.replace(oldPrefix, newPrefix);
      }
      if (!newName || newName === conv.storageKey) continue;
      try {
        await axios.post(`/api/conversations/${encodeURIComponent(conv.storageKey)}/rename`, {
          new_name: newName,
        });
        renameMap[conv.storageKey] = newName;
        if (state.sessionId === conv.storageKey) {
          activeRename = newName;
          setState((prev) => ({
            ...prev,
            sessionId: newName,
            sessionName: displayNameFromId(newName),
          }));
        }
      } catch (err) {
        console.error("Folder rename failed", err);
      }
    }
    setCustomOrder((prev) => {
      const next = { ...(prev || {}) };
      let changed = false;
      Object.entries(renameMap).forEach(([fromKey, toKey]) => {
        const value = Number(next[fromKey]);
        if (!Number.isFinite(value)) return;
        delete next[fromKey];
        next[toKey] = value;
        changed = true;
      });
      return changed ? next : prev;
    });
    const nextSettings = {};
    Object.entries(folderSettings || {}).forEach(([path, meta]) => {
      if (path === oldPath || path.startsWith(oldPrefix)) {
        const updatedPath = path === oldPath ? newPath : path.replace(oldPrefix, newPrefix);
        nextSettings[updatedPath] = meta;
      } else {
        nextSettings[path] = meta;
      }
    });
    await saveFolderSettings(nextSettings);
    await fetchConversations();
    if (activeRename) {
      await loadConversation(activeRename);
    }
  };

  const createFolderPath = async (parentPath, folderName) => {
    if (apiUnavailable) return;
    const base = sanitizeBaseName(folderName);
    if (!base) return;
    const parent = sanitizeFolderPath(parentPath || "");
    const nextPath = sanitizeFolderPath(parent ? `${parent}/${base}` : base);
    if (!nextPath) return;
    if (folderOptions.includes(nextPath)) return;
    const nextSettings = { ...(folderSettings || {}) };
    nextSettings[nextPath] = {
      ...(nextSettings[nextPath] || {}),
    };
    await saveFolderSettings(nextSettings);
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      next.delete(ROOT_FOLDER_KEY);
      if (parent) next.delete(parent);
      next.delete(nextPath);
      return next;
    });
  };

  const moveFolderPathToFolder = async (sourcePath, targetPath) => {
    const source = sanitizeFolderPath(sourcePath || "");
    const target = sanitizeFolderPath(targetPath || "");
    if (!source) return;
    if (target && (target === source || target.startsWith(`${source}/`))) return;
    const segments = splitFolderPath(source);
    const base = segments[segments.length - 1] || "";
    if (!base) return;
    const destination = sanitizeFolderPath(target ? `${target}/${base}` : base);
    if (!destination || destination === source) return;
    await renameFolderPath(source, destination);
  };

  const deleteFolderPath = async (path) => {
    if (apiUnavailable) return;
    const target = sanitizeFolderPath(path || "");
    if (!target) return;
    const segments = splitFolderPath(target);
    const parent = sanitizeFolderPath(segments.slice(0, -1).join("/"));
    const oldPrefix = `${target}/`;
    const renameMap = {};
    let activeRename = null;
    const targets = normalizedConversations.filter(
      (conv) => conv.folderPath === target || conv.folderPath.startsWith(oldPrefix),
    );
    for (const conv of targets) {
      const base = extractSessionBasename(conv.storageKey);
      if (!base) continue;
      const suffix =
        conv.folderPath === target
          ? base
          : conv.storageKey.startsWith(oldPrefix)
            ? conv.storageKey.slice(oldPrefix.length)
            : base;
      const newName = sanitizeFolderPath(parent ? `${parent}/${suffix}` : suffix);
      if (!newName || newName === conv.storageKey) continue;
      try {
        await axios.post(`/api/conversations/${encodeURIComponent(conv.storageKey)}/rename`, {
          new_name: newName,
        });
        renameMap[conv.storageKey] = newName;
        if (state.sessionId === conv.storageKey) {
          activeRename = newName;
          setState((prev) => ({
            ...prev,
            sessionId: newName,
            sessionName: displayNameFromId(newName),
          }));
        }
      } catch (err) {
        console.error("Folder delete/move failed", err);
      }
    }
    setCustomOrder((prev) => {
      const next = { ...(prev || {}) };
      let changed = false;
      Object.entries(renameMap).forEach(([fromKey, toKey]) => {
        const value = Number(next[fromKey]);
        if (!Number.isFinite(value)) return;
        delete next[fromKey];
        next[toKey] = value;
        changed = true;
      });
      return changed ? next : prev;
    });
    const nextSettings = {};
    Object.entries(folderSettings || {}).forEach(([folderPath, meta]) => {
      const clean = sanitizeFolderPath(folderPath);
      if (!clean) return;
      if (clean === target) {
        return;
      }
      if (clean.startsWith(oldPrefix)) {
        const suffix = clean.slice(oldPrefix.length);
        const rebased = sanitizeFolderPath(parent ? `${parent}/${suffix}` : suffix);
        if (!rebased) return;
        nextSettings[rebased] = meta;
        return;
      }
      nextSettings[clean] = meta;
    });
    await saveFolderSettings(nextSettings);
    await fetchConversations();
    if (activeRename) {
      await loadConversation(activeRename);
    }
  };

  const submitRename = async () => {
    if (!renameTarget) return;
    await renameConversationStorage(renameTarget, renameValue);
    closeRenameModal();
  };

  const submitMove = async () => {
    if (!moveTarget) return;
    await moveConversationToFolder(moveTarget, moveFolderValue);
    closeMoveModal();
  };

  const submitFolderRename = async () => {
    if (!folderRenameTarget) return;
    const nextBase = sanitizeBaseName(folderRenameValue);
    if (!nextBase) return;
    const segments = splitFolderPath(folderRenameTarget);
    if (!segments.length) return;
    segments[segments.length - 1] = nextBase;
    const nextPath = sanitizeFolderPath(segments.join("/"));
    if (!nextPath || nextPath === folderRenameTarget) {
      closeFolderRenameModal();
      return;
    }
    await renameFolderPath(folderRenameTarget, nextPath);
    closeFolderRenameModal();
  };

  const submitNewFolder = async () => {
    if (newFolderParentTarget === null) return;
    await createFolderPath(newFolderParentTarget, newFolderValue);
    closeNewFolderModal();
  };

  const submitDeleteFolder = async () => {
    if (!deleteFolderTarget) return;
    await deleteFolderPath(deleteFolderTarget);
    closeDeleteFolderModal();
  };

  const handleRename = (conv) => {
    openRenameModal(conv);
  };

  const handleMove = (conv) => {
    openMoveModal(conv);
  };

  const autoScrollHistoryBody = React.useCallback((clientY) => {
    const container = historyBodyRef.current;
    if (!container || !Number.isFinite(clientY)) return;
    const rect = container.getBoundingClientRect();
    if (!rect || rect.height <= 0) return;
    const topEdge = rect.top + HISTORY_DRAG_SCROLL_EDGE_PX;
    const bottomEdge = rect.bottom - HISTORY_DRAG_SCROLL_EDGE_PX;
    if (clientY < topEdge) {
      const intensity = Math.max(1, Math.min(3, (topEdge - clientY) / 18));
      container.scrollTop -= Math.ceil(HISTORY_DRAG_SCROLL_STEP_PX * intensity);
      return;
    }
    if (clientY > bottomEdge) {
      const intensity = Math.max(1, Math.min(3, (clientY - bottomEdge) / 18));
      container.scrollTop += Math.ceil(HISTORY_DRAG_SCROLL_STEP_PX * intensity);
    }
  }, []);

  const reorderConversationBefore = React.useCallback(
    (dragged, targetConv) => {
      if (!dragged || !targetConv) return;
      const folderPath = targetConv.folderPath || "";
      const sortedFolder = normalizedConversations
        .filter((conv) => (conv.folderPath || "") === folderPath)
        .sort(compareConversations);
      if (!sortedFolder.length) return;
      const withoutDragged = sortedFolder.filter(
        (conv) => conv.storageKey !== dragged.storageKey,
      );
      const targetIndex = withoutDragged.findIndex(
        (conv) => conv.storageKey === targetConv.storageKey,
      );
      if (targetIndex === -1) return;
      withoutDragged.splice(targetIndex, 0, dragged);
      setCustomOrder((prev) => {
        const next = { ...(prev || {}) };
        withoutDragged.forEach((conv, idx) => {
          next[conv.storageKey] = idx;
        });
        return next;
      });
      setSortMode(HISTORY_SORT_MODES.CUSTOM);
    },
    [compareConversations, normalizedConversations],
  );

  const handleConversationDragStart = (event, conv) => {
    if (!conv || apiUnavailable) return;
    if (
      event.target instanceof Element &&
      event.target.closest(".conv-menu-wrapper, .folder-menu-wrapper, a, input, select, textarea")
    ) {
      event.preventDefault();
      return;
    }
    setDraggingFolderPath("");
    setDraggingConversation(conv);
    setDragOverFolder(null);
    setDragOverConversation(null);
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", conv.storageKey);
    } catch {}
  };

  const handleConversationDragEnd = () => {
    setDraggingConversation(null);
    setDragOverFolder(null);
    setDragOverConversation(null);
  };

  const handleFolderDragStart = (event, path) => {
    if (!path || apiUnavailable) return;
    if (
      event.target instanceof Element &&
      event.target.closest(".folder-menu-wrapper, .conv-menu-wrapper, a, input, select, textarea, button")
    ) {
      event.preventDefault();
      return;
    }
    setDraggingConversation(null);
    setDraggingFolderPath(path);
    setDragOverFolder(null);
    setDragOverConversation(null);
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("application/x-float-folder", path);
      event.dataTransfer.setData("text/plain", path);
    } catch {}
  };

  const handleFolderDragEnd = () => {
    setDraggingFolderPath("");
    setDragOverFolder(null);
    setDragOverConversation(null);
  };

  const handleFolderDragOver = (event, folderKey) => {
    if ((!draggingConversation && !draggingFolderPath) || apiUnavailable) return;
    if (
      draggingFolderPath &&
      folderKey &&
      (folderKey === draggingFolderPath || folderKey.startsWith(`${draggingFolderPath}/`))
    ) {
      return;
    }
    event.preventDefault();
    autoScrollHistoryBody(event.clientY);
    try {
      event.dataTransfer.dropEffect = "move";
    } catch {}
    setDragOverFolder(folderKey);
    setDragOverConversation(null);
  };

  const handleFolderDragLeave = (event, folderKey) => {
    if (dragOverFolder !== folderKey) return;
    const related = event.relatedTarget;
    if (related && event.currentTarget.contains(related)) return;
    setDragOverFolder(null);
  };

  const handleFolderDrop = async (event, folderPath) => {
    if (apiUnavailable) return;
    event.preventDefault();
    event.stopPropagation();
    const draggedFolderPath =
      draggingFolderPath || event.dataTransfer.getData("application/x-float-folder");
    if (draggedFolderPath) {
      setDragOverFolder(null);
      setDraggingFolderPath("");
      setDraggingConversation(null);
      setDragOverConversation(null);
      await moveFolderPathToFolder(draggedFolderPath, folderPath || "");
      return;
    }
    const dragged =
      draggingConversation ||
      normalizedConversations.find(
        (conv) => conv.storageKey === event.dataTransfer.getData("text/plain"),
      );
    setDragOverFolder(null);
    setDraggingConversation(null);
    setDragOverConversation(null);
    if (!dragged) return;
    await moveConversationToFolder(dragged, folderPath);
  };

  const handleConversationDragOver = (event, conv) => {
    if (!draggingConversation || draggingFolderPath || apiUnavailable || !conv) return;
    if (draggingConversation.storageKey === conv.storageKey) return;
    event.preventDefault();
    event.stopPropagation();
    autoScrollHistoryBody(event.clientY);
    try {
      event.dataTransfer.dropEffect = "move";
    } catch {}
    setDragOverConversation(conv.storageKey);
    setDragOverFolder(null);
  };

  const handleConversationDragLeave = (event, storageKey) => {
    if (dragOverConversation !== storageKey) return;
    const related = event.relatedTarget;
    if (related && event.currentTarget.contains(related)) return;
    setDragOverConversation(null);
  };

  const handleConversationDrop = async (event, targetConv) => {
    if (!targetConv || apiUnavailable) return;
    event.preventDefault();
    event.stopPropagation();
    const dragged =
      draggingConversation ||
      normalizedConversations.find(
        (conv) => conv.storageKey === event.dataTransfer.getData("text/plain"),
      );
    setDragOverConversation(null);
    setDragOverFolder(null);
    setDraggingConversation(null);
    if (!dragged || dragged.storageKey === targetConv.storageKey) return;
    if ((dragged.folderPath || "") === (targetConv.folderPath || "")) {
      reorderConversationBefore(dragged, targetConv);
      return;
    }
    await moveConversationToFolder(dragged, targetConv.folderPath || "");
  };

  const handleHistoryBodyDragOver = (event) => {
    if ((!draggingConversation && !draggingFolderPath) || apiUnavailable) return;
    event.preventDefault();
    autoScrollHistoryBody(event.clientY);
    try {
      event.dataTransfer.dropEffect = "move";
    } catch {}
  };

  const handleHistoryBodyDrop = async (event) => {
    if ((!draggingConversation && !draggingFolderPath) || apiUnavailable) return;
    const target = event.target;
    if (
      target instanceof Element &&
      (target.closest(".folder-row") || target.closest(".conversation-item"))
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const draggedFolderPath =
      draggingFolderPath || event.dataTransfer.getData("application/x-float-folder");
    if (draggedFolderPath) {
      setDragOverConversation(null);
      setDragOverFolder(null);
      setDraggingConversation(null);
      setDraggingFolderPath("");
      await moveFolderPathToFolder(draggedFolderPath, "");
      return;
    }
    const dragged =
      draggingConversation ||
      normalizedConversations.find(
        (conv) => conv.storageKey === event.dataTransfer.getData("text/plain"),
      );
    setDragOverConversation(null);
    setDragOverFolder(null);
    setDraggingConversation(null);
    setDraggingFolderPath("");
    if (!dragged) return;
    await moveConversationToFolder(dragged, "");
  };

  const openExportModal = (conv) => {
    if (!conv) return;
    setActiveMenuKey(null);
    const defaultName = sanitizeFilename(conv.displayName || conv.storageKey);
    setExportOptions({
      format: exportDefaults.format,
      includeChat: exportDefaults.includeChat,
      includeThoughts: exportDefaults.includeThoughts,
      includeTools: exportDefaults.includeTools,
      filename: defaultName,
    });
    setExportTarget(conv);
  };

  const closeExportModal = () => {
    setExportTarget(null);
  };

  const closeImportReviewModal = () => {
    setImportReview({
      open: false,
      file: null,
      detectedFiles: [],
      selectedFiles: {},
      destinationFolder: "",
    });
  };

  const openConversationInOs = async (conv) => {
    if (!conv) return;
    setActiveMenuKey(null);
    try {
      await axios.get(
        `/api/conversations/reveal/${encodeURIComponent(conv.storageKey)}`,
      );
    } catch (err) {
      console.error("Reveal failed", err);
    }
  };

  const downloadExport = async () => {
    if (!exportTarget) return;
    const fmt = exportOptions.format || "md";
    const params = {
      format: fmt,
      include_chat: exportOptions.includeChat,
      include_thoughts: exportOptions.includeThoughts,
      include_tools: exportOptions.includeTools,
    };
    try {
      const responseType = fmt === "json" ? "json" : "text";
      const res = await axios.get(
        `/api/conversations/${encodeURIComponent(exportTarget.storageKey)}/export`,
        { params, responseType },
      );
      let payloadText = "";
      let mimeType = "text/plain";
      if (fmt === "json") {
        payloadText = JSON.stringify(res.data || {}, null, 2);
        mimeType = "application/json";
      } else if (fmt === "md" || fmt === "markdown") {
        payloadText = String(res.data || "");
        mimeType = "text/markdown";
      } else {
        payloadText = String(res.data || "");
        mimeType = "text/plain";
      }
      const ext = fmt === "markdown" ? "md" : fmt;
      const filename = ensureExtension(exportOptions.filename, ext);
      const blob = new Blob([payloadText], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      closeExportModal();
    } catch (err) {
      console.error("Export failed", err);
    }
  };

  const triggerImportPicker = () => {
    if (apiUnavailable) return;
    setImportStatus("");
    setImportReview((prev) => ({ ...prev, open: false }));
    if (importFileInputRef.current) {
      importFileInputRef.current.value = "";
      importFileInputRef.current.click();
    }
  };

  const openImportReview = (file, detectedFiles) => {
    const normalized = Array.isArray(detectedFiles) ? detectedFiles : [];
    const selectedFiles = {};
    normalized.forEach((item) => {
      const path = String(item?.path || item?.name || "").trim();
      if (!path) return;
      selectedFiles[path] = true;
    });
    if (!normalized.length) {
      setImportStatus("No importable files detected in this archive.");
      return;
    }
    setImportReview({
      open: true,
      file,
      detectedFiles: normalized,
      selectedFiles,
      destinationFolder: "",
    });
  };

  const previewImportCandidates = async (file) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setImportBusy(true);
    setImportStatus("Detecting import candidates...");
    try {
      const response = await axios.post(
        "/api/conversations/import/preview",
        formData,
      );
      const detectedFiles = response.data?.detected_files || [];
      if (Array.isArray(detectedFiles) && detectedFiles.length > 0) {
        openImportReview(file, detectedFiles);
      } else {
        await uploadConversationImport({ file });
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || "Import preview failed";
      setImportStatus(String(detail));
      console.error("Import preview failed", err);
    } finally {
      setImportBusy(false);
    }
  };

  const uploadConversationImport = async ({
    file,
    selectedFiles = null,
    destinationFolder = "",
  }) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("format", inferImportFormatFromFilename(file.name));
    if (Array.isArray(selectedFiles) && selectedFiles.length > 0) {
      formData.append("selected_files", JSON.stringify(selectedFiles));
    }
    if (destinationFolder) {
      formData.append("destination_folder", destinationFolder);
    }
    setImportBusy(true);
    setImportStatus("Importing...");
    let didImport = false;
    try {
      const res = await axios.post("/api/conversations/import", formData);
      const imported = Array.isArray(res.data?.imports) ? res.data.imports : [];
      if (imported.length > 1) {
        setImportStatus(
          `Imported ${imported.length} conversations (${res.data?.message_count || 0} messages).`,
        );
      } else {
        const importedName = String(
          imported?.[0]?.name || res.data?.name || "",
        ).trim();
        setImportStatus(
          importedName
            ? `Imported ${importedName} (${res.data?.message_count || 0} messages).`
            : "Import complete.",
        );
        if (importedName) {
          await loadConversation(importedName);
        }
      }
      await fetchConversations();
      didImport = true;
    } catch (err) {
      const detail = err?.response?.data?.detail || "Import failed";
      setImportStatus(String(detail));
      console.error("Import failed", err);
    } finally {
      setImportBusy(false);
      if (didImport) {
        closeImportReviewModal();
      }
    }
  };

  const handleImportFileChange = (event) => {
    const file = event?.target?.files?.[0];
    if (!file) return;
    const format = inferImportFormatFromFilename(file.name);
    if (format === "zip" || format === "json") {
      previewImportCandidates(file);
      return;
    }
    uploadConversationImport({ file });
  };

  const setImportDestinationFolder = (value) => {
    setImportReview((prev) => ({
      ...prev,
      destinationFolder: (value || "").trim(),
    }));
  };

  const toggleImportFileSelection = (path) => {
    if (!path) return;
    setImportReview((prev) => {
      const nextSelected = { ...(prev.selectedFiles || {}) };
      nextSelected[path] = !nextSelected[path];
      return {
        ...prev,
        selectedFiles: nextSelected,
      };
    });
  };

  const selectAllImportFiles = () => {
    const allSelected = importReview.detectedFiles.every((item) => {
      const path = String(item?.path || item?.name || "").trim();
      return Boolean(path && importReview.selectedFiles?.[path]);
    });
    const nextSelected = {};
    importReview.detectedFiles.forEach((item) => {
      const path = String(item?.path || item?.name || "").trim();
      if (!path) return;
      nextSelected[path] = !allSelected;
    });
    setImportReview((prev) => ({
      ...prev,
      selectedFiles: nextSelected,
    }));
  };

  const confirmImportReview = async () => {
    const selected = Object.entries(importReview.selectedFiles || {}).filter(
      ([, value]) => Boolean(value),
    );
    if (!selected.length) {
      setImportStatus("Select at least one file before importing.");
      return;
    }
    await uploadConversationImport({
      file: importReview.file,
      selectedFiles: selected.map(([path]) => path),
      destinationFolder: importReview.destinationFolder,
    });
  };

  const importReviewTotalCount = importReview.detectedFiles.length;
  const importReviewSelectedCount = Object.values(
    importReview.selectedFiles || {},
  ).filter(Boolean).length;
  const importReviewAllSelected =
    importReviewTotalCount > 0 &&
    importReviewSelectedCount === importReviewTotalCount;

  const handleDelete = async (conv) => {
    if (!conv) return;
    setActiveMenuKey(null);
    const confirmed = window.confirm(`Delete conversation '${conv.displayName}'?`);
    if (!confirmed) return;
    try {
      await axios.delete(`/api/conversations/${encodeURIComponent(conv.storageKey)}`);
      if (state.sessionId === conv.storageKey) {
        setState((prev) => ({
          ...prev,
          conversation: [],
          sessionId: "",
          sessionName: "",
        }));
      }
      await fetchConversations();
    } catch (err) {
      console.error("Delete failed", err);
    }
  };

  const handleThreadTagClick = (tag) => {
    if (!tag) return;
    setActiveThreadFilter(tag);
    setActiveMenuKey(null);
  };

  const openThreadsPage = (tag) => {
    const query = tag ? `?tab=threads&thread=${encodeURIComponent(tag)}` : "?tab=threads";
    navigate(`/knowledge${query}`);
    setActiveMenuKey(null);
  };

  const renderConversationRow = (conv, depth = 0) => {
    const indent = depth > 0 ? depth * 14 : 0;
    const threadColor = getThreadColor(conv.threadId || conv.storageKey);
    const isActive = state.sessionId === conv.storageKey;
    const isDragging = draggingConversation?.storageKey === conv.storageKey;
    const isDropTarget = dragOverConversation === conv.storageKey;
    const isMenuOpen = activeMenuKey === conv.storageKey;
    const dateSortMode =
      sortMode === HISTORY_SORT_MODES.CREATED
        ? HISTORY_SORT_MODES.CREATED
        : HISTORY_SORT_MODES.UPDATED;
    const dateToggleTarget =
      dateSortMode === HISTORY_SORT_MODES.CREATED
        ? HISTORY_SORT_MODES.UPDATED
        : HISTORY_SORT_MODES.CREATED;
    const sortPrefix =
      sortMode === HISTORY_SORT_MODES.CREATED
        ? "C"
        : sortMode === HISTORY_SORT_MODES.ALPHABETICAL
          ? "A"
          : sortMode === HISTORY_SORT_MODES.CUSTOM
            ? "#"
            : "U";
    return (
      <div
        key={`conv-${conv.storageKey}`}
        className={`conversation-item folder-conversation${
          isActive ? " is-active" : ""
        }${isDragging ? " is-dragging" : ""}${isDropTarget ? " is-drop-target" : ""}${
          isMenuOpen ? " menu-open" : ""
        }`}
        style={indent ? { paddingLeft: indent } : undefined}
        draggable={!apiUnavailable}
        onDragStart={(event) => handleConversationDragStart(event, conv)}
        onDragEnd={handleConversationDragEnd}
        onDragOver={(event) => handleConversationDragOver(event, conv)}
        onDragLeave={(event) => handleConversationDragLeave(event, conv.storageKey)}
        onDrop={(event) => handleConversationDrop(event, conv)}
      >
        <div className="conversation-main">
          <span className="thread-dot" style={{ backgroundColor: threadColor }} aria-hidden="true" />
          <button
            className="conversation-link"
            onClick={() => loadConversation(conv.storageKey)}
            title={conv.displayName}
            onDoubleClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (!apiUnavailable) handleRename(conv);
            }}
          >
            {conv.displayName}
          </button>
        </div>
        <div className="conv-actions">
          <button
            type="button"
            className="conversation-date-tag"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setSortMode((prev) =>
                prev === HISTORY_SORT_MODES.CREATED
                  ? HISTORY_SORT_MODES.UPDATED
                  : HISTORY_SORT_MODES.CREATED,
              );
            }}
            title={`Switch date tag to ${dateToggleTarget === HISTORY_SORT_MODES.CREATED ? "created" : "updated"} date. Updated ${
              conv.updatedAt ? new Date(conv.updatedAt).toLocaleString() : "--"
            } | Created ${conv.createdAt ? new Date(conv.createdAt).toLocaleString() : "--"}`}
          >
            <span className="conversation-date-row">
              <span className="date-prefix">{sortPrefix}</span>
              {formatDateLabel(
                dateSortMode === HISTORY_SORT_MODES.CREATED
                  ? conv.createdAt
                  : conv.updatedAt,
                conv.storageKey,
              )}
            </span>
          </button>
          <div className="conv-menu-wrapper">
            <button
              type="button"
              className="conv-menu-button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                event.preventDefault();
                setActiveMenuKey((prev) => (prev === conv.storageKey ? null : conv.storageKey))
              }}
              aria-label="Conversation options"
              title="Conversation options"
            >
              &#8942;
            </button>
            {isMenuOpen && (
              <div
                className="conv-menu"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
              >
                <div className="conv-menu-card">
                  <div className="history-prop-row">
                    <span className="history-prop-label">Name</span>
                    <button
                      type="button"
                      className="history-prop-link"
                      title="Double click to rename"
                      onDoubleClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        if (!apiUnavailable) handleRename(conv);
                      }}
                    >
                      {conv.displayName}
                    </button>
                  </div>
                  <div className="history-prop-row">
                    <span className="history-prop-label">Storage key</span>
                    <button
                      type="button"
                      className="history-prop-link"
                      title="Open on host"
                      onClick={() => openConversationInOs(conv)}
                      disabled={apiUnavailable}
                    >
                      {conv.storageKey}
                    </button>
                  </div>
                  {conv.threadTags && conv.threadTags.length ? (
                    <div className="history-prop-row history-prop-row--stack">
                      <span className="history-prop-label">Threads</span>
                      <div className="thread-tag-row">
                        <div className="thread-tag-scroll-wrap">
                          <div className="thread-tag-scroll">
                            {conv.threadTags.map((tag) => (
                              <button
                                key={tag}
                                type="button"
                                className={`thread-tag-chip${
                                  activeThreadTag === tag ? " active" : ""
                                }`}
                                onClick={() => {
                                  setActiveThreadTag(tag);
                                  handleThreadTagClick(tag);
                                }}
                                onDoubleClick={() => openThreadsPage(tag)}
                                title={`Filter to thread ${tag}`}
                              >
                                <span
                                  className="thread-tag-dot"
                                  style={{ backgroundColor: getThreadColor(tag) }}
                                  aria-hidden="true"
                                />
                                <span className="thread-tag-label">{tag}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="thread-tag-open"
                          onClick={() => openThreadsPage(activeThreadTag || conv.threadTags[0])}
                          title="Open threads page"
                        >
                          threads
                        </button>
                      </div>
                    </div>
                  ) : null}
                  {conv.createdAt ? (
                    <div className="history-prop-row">
                      <span className="history-prop-label">Created</span>
                      <span className="history-prop-value">
                        {new Date(conv.createdAt).toLocaleString()}
                      </span>
                    </div>
                  ) : null}
                  {conv.updatedAt ? (
                    <div className="history-prop-row">
                      <span className="history-prop-label">Updated</span>
                      <span className="history-prop-value">
                        {new Date(conv.updatedAt).toLocaleString()}
                      </span>
                    </div>
                  ) : null}
                  {conv.messageCount !== null ? (
                    <div className="history-prop-row">
                      <span className="history-prop-label">Messages</span>
                      <span className="history-prop-value">{conv.messageCount}</span>
                    </div>
                  ) : null}
                </div>
                <div className="conv-menu-actions">
                  <button
                    type="button"
                    onClick={() => handleRename(conv)}
                    disabled={apiUnavailable}
                  >
                    Rename
                  </button>
                  <button type="button" onClick={() => handleMove(conv)} disabled={apiUnavailable}>
                    Move
                  </button>
                  <button type="button" onClick={() => openExportModal(conv)}>
                    Export
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(conv)}
                    disabled={apiUnavailable}
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => openConversationInOs(conv)}
                    disabled={apiUnavailable}
                  >
                    Open in filesystem
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderFolderNode = (node, depth = 0) => {
    const isRoot = !node.path;
    const folderKey = isRoot ? ROOT_FOLDER_KEY : node.path;
    const collapsedNode = collapsedFolders.has(folderKey);
    const isTopLevelFolder = !isRoot && !node.path.includes("/");
    const shouldCollapseTopLevelScaffold =
      isTopLevelFolder &&
      node.conversations.length === 0 &&
      (node.childList || []).length === 1;
    if (shouldCollapseTopLevelScaffold) {
      const [collapsedChild] = node.childList || [];
      return collapsedChild ? renderFolderNode(collapsedChild, depth) : null;
    }
    const nextDepth = isRoot ? depth : depth + 1;
    const label = isRoot ? "Conversations" : getFolderLabel(node.path, node.name);
    const accent = isRoot ? "" : getFolderColor(node.path);
    const isDragOver = dragOverFolder === folderKey;
    const isFolderMenuOpen = !isRoot && folderMenuKey === node.path;
    const nodeStyle = accent ? { "--folder-accent": accent } : undefined;
    return (
      <div
        key={`folder-${node.path || "root"}`}
        className={`folder-node${isRoot ? " root" : ""}`}
        style={nodeStyle}
      >
        <div
          className={`folder-row${collapsedNode ? " collapsed" : ""}${
            isDragOver ? " drag-over" : ""
          }${isFolderMenuOpen ? " menu-open" : ""}`}
          style={{ paddingLeft: Math.max(depth * 14, 0) }}
          draggable={!isRoot && !apiUnavailable}
          role="button"
          tabIndex={0}
          onClick={() => toggleFolder(node.path)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleFolder(node.path);
            }
          }}
          aria-expanded={!collapsedNode}
          onDragOver={(event) => handleFolderDragOver(event, folderKey)}
          onDragLeave={(event) => handleFolderDragLeave(event, folderKey)}
          onDrop={(event) => handleFolderDrop(event, node.path)}
          onDragStart={(event) => {
            if (isRoot) return;
            handleFolderDragStart(event, node.path);
          }}
          onDragEnd={handleFolderDragEnd}
        >
          <span className="folder-caret" aria-hidden="true">
            {collapsedNode ? "\u25B8" : "\u25BE"}
          </span>
          <span className="folder-dot" aria-hidden="true" />
          <span className="folder-name">{label}</span>
          <span className="folder-count">{node.conversations.length}</span>
          {!isRoot && (
            <div className="folder-menu-wrapper">
              <button
                type="button"
                className="folder-menu-button"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  event.preventDefault();
                  setFolderMenuKey((prev) => (prev === node.path ? null : node.path));
                  setActiveMenuKey(null);
                }}
                aria-label="Folder options"
                title="Folder options"
              >
                &#8942;
              </button>
              {folderMenuKey === node.path && (
                <div
                  className="folder-menu"
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => event.stopPropagation()}
                >
                  <button
                    type="button"
                    className="folder-menu-action"
                    onClick={(event) => {
                      event.stopPropagation();
                      openNewFolderModal(node.path);
                    }}
                    disabled={apiUnavailable}
                  >
                    New subfolder
                  </button>
                  <button
                    type="button"
                    className="folder-menu-action"
                    onClick={(event) => {
                      event.stopPropagation();
                      openFolderRenameModal(node.path);
                    }}
                    disabled={apiUnavailable}
                  >
                    Rename folder
                  </button>
                  <button
                    type="button"
                    className="folder-menu-action danger"
                    onClick={(event) => {
                      event.stopPropagation();
                      openDeleteFolderModal(node.path);
                    }}
                    disabled={apiUnavailable}
                  >
                    Delete folder
                  </button>
                  <div className="folder-menu-section">
                    <span className="folder-menu-label">Color tag</span>
                    <div className="folder-color-grid">
                      {DEFAULT_FOLDER_COLORS.map((color) => (
                        <button
                          key={color}
                          type="button"
                          className={`folder-color-chip${
                            getFolderColor(node.path) === color ? " active" : ""
                          }`}
                          style={{ backgroundColor: color }}
                          onClick={(event) => {
                            event.stopPropagation();
                            const current = getFolderColor(node.path);
                            updateFolderMeta(node.path, {
                              color: current === color ? "" : color,
                            });
                          }}
                          title={`Set folder color ${color}`}
                        />
                      ))}
                      <button
                        type="button"
                        className="folder-color-chip clear"
                        onClick={(event) => {
                          event.stopPropagation();
                          updateFolderMeta(node.path, { color: "" });
                        }}
                        title="Clear folder color"
                      >
                        x
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        {isRoot ? (
          <div className="folder-children">
            {!collapsedNode &&
              node.conversations.map((conv) => renderConversationRow(conv, nextDepth))}
            {(node.childList || []).map((child) => renderFolderNode(child, nextDepth))}
          </div>
        ) : (
          !collapsedNode && (
            <div className="folder-children">
              {node.conversations.map((conv) => renderConversationRow(conv, nextDepth))}
              {(node.childList || []).map((child) => renderFolderNode(child, nextDepth))}
            </div>
          )
        )}
      </div>
    );
  };

  const renderThreadGroup = (group) => {
    const groupId = String(group?.id || "ungrouped");
    const isCollapsed = collapsedThreadGroups.has(groupId);
    const threadLabel = group.label || "Thread";
    const threadColor = ensureString(group?.color) || "var(--color-lavender)";
    return (
      <div
        key={`thread-${group.id}`}
        className="thread-group"
        style={{ "--thread-group-accent": threadColor }}
      >
        <div
          className={`thread-group-header${isCollapsed ? " collapsed" : ""}`}
          role="button"
          tabIndex={0}
          aria-expanded={!isCollapsed}
          onClick={() => toggleThreadGroup(groupId)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleThreadGroup(groupId);
            }
          }}
          title={`${isCollapsed ? "Expand" : "Collapse"} ${threadLabel}`}
        >
          <button
            type="button"
            className="thread-group-caret"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              toggleThreadGroup(groupId);
            }}
            aria-expanded={!isCollapsed}
            aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${threadLabel}`}
            title={`${isCollapsed ? "Expand" : "Collapse"} ${threadLabel}`}
          >
            {isCollapsed ? "\u25B8" : "\u25BE"}
          </button>
          <span
            className="thread-dot"
            style={{ backgroundColor: threadColor }}
            aria-hidden="true"
          />
          <span className="thread-title">{threadLabel}</span>
          <button
            type="button"
            className="thread-group-open"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              openThreadsPage(group.id === "ungrouped" ? null : group.id);
            }}
            title={`Open ${threadLabel} in Threads`}
          >
            Open
          </button>
          <span className="thread-count">{group.conversations.length}</span>
        </div>
        {!isCollapsed && (
          <div className="thread-group-body">
            {group.conversations.map((conv) => renderConversationRow(conv, 0))}
          </div>
        )}
      </div>
    );
  };

  const isThreadGroupsCollapsed = collapsedThreadGroups.has(THREAD_GROUP_ROOT_KEY);

  return (
    <aside
      ref={sidebarRef}
      className={`sidebar left-sidebar${collapsed ? " collapsed" : ""}`}
    >
      <button
        className="collapse-btn"
        onClick={(event) =>
          handleUnifiedPress(event, () => {
            const btn = event.currentTarget;
            if (btn.__hoverTimer) {
              clearTimeout(btn.__hoverTimer);
              btn.__hoverTimer = null;
            }
            if (btn.__lastHoverToggleAt && Date.now() - btn.__lastHoverToggleAt < 300) {
              return;
            }
            onToggle?.();
          })
        }
        onPointerDown={(event) =>
          handleUnifiedPress(event, () => {
            const btn = event.currentTarget;
            if (btn.__hoverTimer) {
              clearTimeout(btn.__hoverTimer);
              btn.__hoverTimer = null;
            }
            if (btn.__lastHoverToggleAt && Date.now() - btn.__lastHoverToggleAt < 300) {
              return;
            }
            onToggle?.();
          })
        }
        onMouseEnter={(e) => {
          if (!supportsHoverInteractions()) return;
          const btn = e.currentTarget;
          if (btn.__hoverTimer) clearTimeout(btn.__hoverTimer);
          btn.__hoverTimer = setTimeout(() => {
            btn.__lastHoverToggleAt = Date.now();
            onToggle?.();
          }, 1000);
        }}
        onMouseLeave={(e) => {
          const btn = e.currentTarget;
          if (btn.__hoverTimer) {
            clearTimeout(btn.__hoverTimer);
            btn.__hoverTimer = null;
          }
        }}
        aria-label="Collapse history sidebar"
        title="Collapse history sidebar"
      >
        {"<"}
      </button>
      <div className="sidebar-header history-header">
        <div className="history-title">
          <h2>history</h2>
        </div>
        <div className="history-toggle" role="group" aria-label="History view">
          <button
            type="button"
            className={historyMode === HISTORY_VIEW_MODES.THREADS ? "active" : ""}
            onClick={() => setHistoryMode(HISTORY_VIEW_MODES.THREADS)}
            title="View conversations grouped by thread tags"
          >
            threads
          </button>
          <button
            type="button"
            className={historyMode === HISTORY_VIEW_MODES.FOLDERS ? "active" : ""}
            onClick={() => setHistoryMode(HISTORY_VIEW_MODES.FOLDERS)}
            title="View conversations grouped by folders"
          >
            folders
          </button>
        </div>
        <div className="history-controls">
          <div className="history-controls-left">
            <div className="history-controls-scroll" ref={historyControlsScrollRef}>
              <div className="history-controls-scroll-content" ref={historyControlsContentRef}>
                <div className="history-utilities">
                  <button
                    type="button"
                    className="history-sort-btn"
                    onClick={toggleSortMode}
                    aria-pressed={sortMode === HISTORY_SORT_MODES.CUSTOM}
                    title={`sort mode: ${HISTORY_SORT_MODE_LABELS[sortMode] || "updated"}. click to cycle.`}
                  >
                    <SwapVertIcon className="history-btn-icon" fontSize="inherit" aria-hidden="true" />
                    <span>{HISTORY_SORT_MODE_LABELS[sortMode] || "updated"}</span>
                  </button>
                </div>
                <div className="history-actions">
                  <input
                    ref={importFileInputRef}
                    type="file"
                    style={{ display: "none" }}
                    onChange={handleImportFileChange}
                    accept=".md,.markdown,.txt,.json,.zip"
                  />
                  <button
                    className="new-chat-btn history-action-btn"
                    onClick={triggerImportPicker}
                    title="Import markdown, json, or OpenAI export zip"
                    disabled={apiUnavailable || importBusy}
                  >
                    {importBusy ? "importing..." : "import"}
                  </button>
                  <button
                    className="new-chat-btn history-action-btn"
                    onClick={forkConversation}
                    title="Fork current conversation to a new chat"
                    disabled={apiUnavailable}
                  >
                    <CallSplitIcon className="history-btn-icon" fontSize="inherit" aria-hidden="true" />
                    <span>fork</span>
                  </button>
                  <button
                    className="new-chat-btn history-action-btn"
                    onClick={() => openNewFolderModal("")}
                    title="Create a new folder under the selected path"
                    disabled={apiUnavailable}
                  >
                    new folder
                  </button>
                </div>
              </div>
            </div>
            {historyControlsIndicator.hasOverflow ? (
              <div className="history-scroll-indicator" aria-hidden="true">
                <span
                  className="history-scroll-indicator-thumb"
                  style={{
                    left: `${historyControlsIndicator.thumbOffset * 100}%`,
                    width: `${historyControlsIndicator.thumbWidth * 100}%`,
                  }}
                />
              </div>
            ) : null}
          </div>
          <div className="history-controls-right">
            <button
              className="new-chat-btn history-action-btn history-action-btn-primary"
              onClick={newChat}
              title="Start new chat"
            >
              new chat
            </button>
          </div>
        </div>
        {importStatus ? (
          <div className="history-empty" style={{ paddingTop: "4px", marginTop: "4px" }}>
            {importStatus}
          </div>
        ) : null}
      </div>
      {importReview.open &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeImportReviewModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Import conversations</h3>
            <div className="history-modal-body">
              <label className="history-modal-field">
                <span>Destination folder</span>
                <input
                  type="text"
                  value={importReview.destinationFolder}
                  onChange={(event) => setImportDestinationFolder(event.target.value)}
                  placeholder="Leave blank for root"
                />
              </label>
              <div className="history-chip-row">
                <button
                  type="button"
                  className={`history-chip${!importReview.destinationFolder ? " active" : ""}`}
                  onClick={() => setImportDestinationFolder("")}
                  title="Import into root folder"
                >
                  Root
                </button>
                {folderOptions.map((path) => (
                  <button
                    key={`import-destination-${path}`}
                    type="button"
                    className={`history-chip${
                      importReview.destinationFolder === path ? " active" : ""
                    }`}
                    onClick={() => setImportDestinationFolder(path)}
                    title={path}
                  >
                    {getFolderDisplayLabel(path, path)}
                  </button>
                ))}
              </div>
              <div className="history-modal-field">
                <div className="history-import-toolbar">
                  <span>
                    Detected files ({importReviewSelectedCount}/{importReviewTotalCount})
                  </span>
                  <button type="button" onClick={selectAllImportFiles}>
                    {importReviewAllSelected ? "Deselect all" : "Select all"}
                  </button>
                </div>
                <div className="history-import-list">
                  {importReview.detectedFiles.length === 0 ? (
                    <div className="history-empty">No files detected.</div>
                  ) : (
                    importReview.detectedFiles.map((item) => {
                      const path = String(item?.path || item?.name || "").trim();
                      if (!path) return null;
                      const checked = Boolean(importReview.selectedFiles[path]);
                      return (
                        <label key={`import-file-${path}`} className="history-import-item">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleImportFileSelection(path)}
                          />
                          <div className="history-import-item-meta">
                            <span className="history-import-item-path">{path}</span>
                            <span className="history-import-item-count">
                              {item.message_count ?? 0} messages
                            </span>
                          </div>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeImportReviewModal}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={confirmImportReview}
                disabled={importReviewSelectedCount === 0 || importBusy}
              >
                OK
              </button>
            </div>
          </div>
        </div>,
        )}
      {activeThreadFilter && (
        <div className="history-filter-bar">
          <span>
            Thread filter: <strong>{activeThreadFilter}</strong>
          </span>
          <button type="button" onClick={() => setActiveThreadFilter(null)}>
            Clear
          </button>
        </div>
      )}
      <div
        ref={historyBodyRef}
        className="history-body"
        onDragOver={handleHistoryBodyDragOver}
        onDrop={handleHistoryBodyDrop}
      >
        {apiUnavailable ? (
          <p className="history-empty">History unavailable while API is offline.</p>
        ) : visibleConversations.length === 0 ? (
          <p className="history-empty">
            {activeThreadFilter
              ? `No conversations tagged ${activeThreadFilter}.`
              : "No conversations yet."}
          </p>
        ) : historyMode === HISTORY_VIEW_MODES.THREADS ? (
          <div className="thread-groups">
            <div
              className={`thread-group-header thread-group-root${
                isThreadGroupsCollapsed ? " collapsed" : ""
              }`}
              style={{ "--thread-group-accent": "var(--color-lavender)" }}
              role="button"
              tabIndex={0}
              aria-expanded={!isThreadGroupsCollapsed}
              onClick={() => toggleThreadGroup(THREAD_GROUP_ROOT_KEY)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  toggleThreadGroup(THREAD_GROUP_ROOT_KEY);
                }
              }}
              title={`${isThreadGroupsCollapsed ? "Expand" : "Collapse"} Threads`}
            >
              <button
                type="button"
                className="thread-group-caret"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  toggleThreadGroup(THREAD_GROUP_ROOT_KEY);
                }}
                aria-expanded={!isThreadGroupsCollapsed}
                aria-label={`${isThreadGroupsCollapsed ? "Expand" : "Collapse"} all threads`}
                title={`${isThreadGroupsCollapsed ? "Expand" : "Collapse"} all threads`}
              >
                {isThreadGroupsCollapsed ? "\u25B8" : "\u25BE"}
              </button>
              <span className="thread-dot" aria-hidden="true" />
              <span className="thread-title">Threads</span>
              <button
                type="button"
                className="thread-group-open"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  openThreadsPage();
                }}
                title="Open Threads tab"
              >
                Open
              </button>
              <span className="thread-count">
                {visibleThreadGroups.reduce(
                  (sum, group) => sum + group.conversations.length,
                  0,
                )}
              </span>
            </div>
            {!isThreadGroupsCollapsed &&
              visibleThreadGroups.map((group) => renderThreadGroup(group))}
          </div>
        ) : (
          <div className="folder-tree">{renderFolderNode(visibleFolderTree, 0)}</div>
        )}
      </div>
      {exportTarget &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeExportModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Export conversation</h3>
            <div className="history-modal-body">
              <label className="history-modal-field">
                <span>Format</span>
                <select
                  value={exportOptions.format}
                  onChange={(event) =>
                    setExportOptions((prev) => ({ ...prev, format: event.target.value }))
                  }
                >
                  <option value="md">Markdown</option>
                  <option value="json">JSON</option>
                  <option value="text">Text</option>
                </select>
              </label>
              <div className="history-modal-field">
                <span>Channels</span>
                <div className="history-channel-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={exportOptions.includeChat}
                      onChange={(event) =>
                        setExportOptions((prev) => ({
                          ...prev,
                          includeChat: event.target.checked,
                        }))
                      }
                    />
                    Chat
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={exportOptions.includeThoughts}
                      onChange={(event) =>
                        setExportOptions((prev) => ({
                          ...prev,
                          includeThoughts: event.target.checked,
                        }))
                      }
                    />
                    Thoughts
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={exportOptions.includeTools}
                      onChange={(event) =>
                        setExportOptions((prev) => ({
                          ...prev,
                          includeTools: event.target.checked,
                        }))
                      }
                    />
                    Tools
                  </label>
                </div>
              </div>
              <label className="history-modal-field">
                <span>Filename</span>
                <input
                  type="text"
                  value={exportOptions.filename}
                  onChange={(event) =>
                    setExportOptions((prev) => ({ ...prev, filename: event.target.value }))
                  }
                />
              </label>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeExportModal}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={downloadExport}>
                Download
              </button>
            </div>
          </div>
        </div>,
        )}
      {renameTarget &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeRenameModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Rename conversation</h3>
            <div className="history-modal-body">
              <div className="history-modal-field">
                <span>Folder</span>
                <div className="history-modal-helper">
                  {getFolderPathFromName(renameTarget.storageKey) || "Root"}
                </div>
              </div>
              <label className="history-modal-field">
                <span>Name</span>
                <input
                  type="text"
                  value={renameValue}
                  onChange={(event) => setRenameValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submitRename();
                    }
                  }}
                />
              </label>
              <div className="history-channel-row">
                <button
                  type="button"
                  onClick={suggestRenameValue}
                  disabled={renameSuggestBusy || apiUnavailable}
                >
                  {renameSuggestBusy ? "Suggesting..." : "Suggest name"}
                </button>
              </div>
              <div className="history-modal-note">
                Folder path stays the same. Use Move to change folders.
              </div>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeRenameModal}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={submitRename}>
                Save
              </button>
            </div>
          </div>
        </div>,
        )}
      {moveTarget &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeMoveModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Move conversation</h3>
            <div className="history-modal-body">
              <div className="history-modal-field">
                <span>Conversation</span>
                <div className="history-modal-helper">{moveTarget.displayName}</div>
              </div>
              <label className="history-modal-field">
                <span>Folder path</span>
                <input
                  type="text"
                  value={moveFolderValue}
                  onChange={(event) => setMoveFolderValue(event.target.value)}
                  placeholder="Leave blank for root"
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submitMove();
                    }
                  }}
                />
              </label>
              <div className="history-modal-note">
                Use / for nested folders. Leave blank for root.
              </div>
              <div className="history-chip-row">
                <button
                  type="button"
                  className={`history-chip${moveFolderValue ? "" : " active"}`}
                  onClick={() => setMoveFolderValue("")}
                >
                  Root
                </button>
                {folderOptions.map((path) => (
                  <button
                    key={path}
                    type="button"
                    className={`history-chip${
                      moveFolderValue === path ? " active" : ""
                    }`}
                    onClick={() => setMoveFolderValue(path)}
                    title={path}
                  >
                    {getFolderDisplayLabel(path, path)}
                  </button>
                ))}
              </div>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeMoveModal}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={submitMove}>
                Move
              </button>
            </div>
          </div>
        </div>,
        )}
      {folderRenameTarget &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeFolderRenameModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Rename folder</h3>
            <div className="history-modal-body">
              <div className="history-modal-field">
                <span>Current path</span>
                <div className="history-modal-helper">{folderRenameTarget}</div>
              </div>
              <label className="history-modal-field">
                <span>New name</span>
                <input
                  type="text"
                  value={folderRenameValue}
                  onChange={(event) => setFolderRenameValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submitFolderRename();
                    }
                  }}
                />
              </label>
              <div className="history-modal-note">
                Only the last segment is renamed.
              </div>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeFolderRenameModal}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={submitFolderRename}>
                Save
              </button>
            </div>
          </div>
        </div>,
        )}
      {newFolderParentTarget !== null &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeNewFolderModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>New folder</h3>
            <div className="history-modal-body">
              <label className="history-modal-field">
                <span>Parent path</span>
                <input
                  type="text"
                  value={newFolderParentTarget || ""}
                  onChange={(event) =>
                    setNewFolderParentTarget(sanitizeFolderPath(event.target.value))
                  }
                  placeholder="Leave blank for root"
                  list="history-new-folder-parent-options"
                />
                <datalist id="history-new-folder-parent-options">
                  {folderOptions.map((path) => (
                    <option key={`new-folder-parent-${path}`} value={path} />
                  ))}
                </datalist>
              </label>
              <div className="history-modal-note">
                Use / for nested folders. Leave blank for root.
              </div>
              <label className="history-modal-field">
                <span>Folder name</span>
                <input
                  type="text"
                  value={newFolderValue}
                  onChange={(event) => setNewFolderValue(event.target.value)}
                  placeholder="e.g. projects"
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submitNewFolder();
                    }
                  }}
                />
              </label>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeNewFolderModal}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={submitNewFolder}
                disabled={!sanitizeBaseName(newFolderValue)}
              >
                Create
              </button>
            </div>
          </div>
        </div>,
        )}
      {deleteFolderTarget &&
        renderInBodyPortal(
        <div className="history-modal-overlay" onClick={closeDeleteFolderModal}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <h3>Delete folder</h3>
            <div className="history-modal-body">
              <div className="history-modal-field">
                <span>Folder path</span>
                <div className="history-modal-helper">{deleteFolderTarget}</div>
              </div>
              <div className="history-modal-note">
                Conversations and subfolders move to the parent folder.
              </div>
            </div>
            <div className="history-modal-actions">
              <button type="button" onClick={closeDeleteFolderModal}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={submitDeleteFolder}>
                Delete
              </button>
            </div>
          </div>
        </div>,
        )}
      <div
        className={`sidebar-resizer${isResizing ? " is-resizing" : ""}`}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize history sidebar"
        title="Drag to resize. Shift + Arrow keys resize faster. Home resets width."
        onPointerDown={startResize}
        onDoubleClick={resetSidebarWidth}
        onKeyDown={handleResizeKeyDown}
        tabIndex={0}
      />
    </aside>
  );
};

export default HistorySidebar;
