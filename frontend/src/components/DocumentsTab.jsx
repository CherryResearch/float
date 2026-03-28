import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import CsvTablePreview from "./CsvTablePreview";
import MediaViewer from "./MediaViewer";
import FilterBar from "./FilterBar";
import { TABULAR_DOC_EXTENSIONS } from "../utils/csvPreview";
import {
  EDITABLE_TEXT_DOC_EXTENSIONS,
  getDocumentExtension,
  MARKDOWN_DOC_EXTENSIONS,
} from "../utils/documentFormats";

const DOC_SEARCH_MODES = {
  CATALOG: "catalog",
  SEMANTIC: "semantic",
};

const ATTACHMENT_FOLDER_ASSIGNMENTS_STORAGE_KEY =
  "documentsAttachmentFolderAssignments";
const ATTACHMENT_FOLDER_ORDER_STORAGE_KEY = "documentsAttachmentFolderOrder";
const ATTACHMENT_FOLDER_ALL = "__all__";
const ATTACHMENT_FOLDER_UNSORTED = "__unsorted__";

const DOC_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "svg", "webp"]);
const DOC_VIDEO_EXTENSIONS = new Set(["mp4", "webm"]);
const DOC_AUDIO_EXTENSIONS = new Set(["mp3", "wav"]);
const TEXT_NOTE_DOC_KINDS = new Set(["note", "text", "markdown"]);

const formatBytes = (value) => {
  if (typeof value !== "number" || Number.isNaN(value)) return "";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = -1;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[Math.max(unitIndex, 0)]}`;
};

const formatTimestamp = (value) => {
  if (!value) return "";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString();
  } catch {
    return "";
  }
};

export const buildAttachmentViewerItems = (items = []) =>
  items.map((att) => ({
    src: att.url,
    alt: att.filename || att.content_hash,
    label: att.filename || att.content_hash,
    size: typeof att.size === "number" ? att.size : null,
    uploadedAt: att.uploaded_at || null,
    contentHash: att.content_hash || null,
    caption: att.caption || "",
    captionStatus: att.caption_status || "",
    placeholderCaption: att.placeholder_caption === true,
    origin: att.origin || "",
    sourceSyncLabel: att.source_sync_label || "",
    sourceSyncNamespace: att.source_sync_namespace || "",
    relativePath: att.relative_path || "",
    captureSource: att.capture_source || "",
    indexStatus: att.index_status || "",
  }));

export const describeAttachmentCard = (attachment, folderLabel) => {
  const att = attachment && typeof attachment === "object" ? attachment : {};
  const label = att.filename || att.content_hash || "";
  const captionText = typeof att.caption === "string" ? att.caption.trim() : "";
  const pathLabel = stripDataFilesPrefix(att.relative_path || "");
  const secondaryMeta = [
    att.size ? formatBytes(att.size) : "",
    att.uploaded_at ? formatTimestamp(att.uploaded_at) : "",
    att.capture_source ? `${att.origin || "capture"}:${att.capture_source}` : "",
    att.source_sync_label ? `from ${att.source_sync_label}` : "",
    !att.capture_source && att.origin ? att.origin : "",
    pathLabel || "",
  ].filter(Boolean);
  const badges = [
    {
      key: "folder",
      label: folderLabel,
      title: `folder: ${folderLabel}`,
      className: "attachment-badge",
    },
    att.origin
      ? {
          key: "origin",
          label: att.origin,
          title: att.relative_path || att.origin,
          className: "attachment-badge attachment-badge--origin",
        }
      : null,
    att.source_sync_label
      ? {
          key: "source",
          label: `from ${att.source_sync_label}`,
          title: att.source_sync_namespace
            ? `synced from ${att.source_sync_label} (${att.source_sync_namespace})`
            : `synced from ${att.source_sync_label}`,
          className: "attachment-badge attachment-badge--source",
        }
      : null,
    att.index_status
      ? {
          key: "index",
          label: att.index_status,
          title: att.index_warning
            ? `index: ${att.index_status} (${att.index_warning})`
            : `index: ${att.index_status}`,
          className: "attachment-badge attachment-badge--status",
        }
      : null,
    att.caption_status
      ? {
          key: "caption",
          label: att.caption_status,
          title: `caption: ${att.caption_status}`,
          className: "attachment-badge attachment-badge--status",
        }
      : null,
    att.placeholder_caption
      ? {
          key: "placeholder",
          label: "placeholder",
          title: "Placeholder caption",
          className: "attachment-badge attachment-badge--placeholder",
        }
      : null,
  ].filter(Boolean);
  return { label, captionText, secondaryMeta, badges };
};

const normalizePath = (value) => {
  if (typeof value !== "string") return "";
  return value.replace(/\\/g, "/").trim();
};

const stripDataFilesPrefix = (value) => {
  if (!value) return "";
  const normalized = normalizePath(value);
  const lower = normalized.toLowerCase();
  const candidates = ["data/files/", "/data/files/", "files/"];
  for (const token of candidates) {
    const idx = lower.lastIndexOf(token);
    if (idx !== -1) {
      return normalized.slice(idx + token.length);
    }
  }
  return normalized;
};

const isLikelyAbsolutePath = (value) => {
  const normalized = normalizePath(value);
  if (!normalized) return false;
  return /^[a-z]:\//i.test(normalized) || normalized.startsWith("/") || normalized.startsWith("//");
};

const appearsUnderDataFiles = (value) => {
  const lower = normalizePath(value).toLowerCase();
  return lower.includes("/data/files/") || lower.startsWith("data/files/") || lower.startsWith("files/");
};

const splitFolderPath = (path) => {
  if (!path) return [];
  return String(path)
    .split("/")
    .map((segment) => segment.trim())
    .filter(Boolean);
};

const getDocRelativePath = (meta) => {
  if (!meta || typeof meta !== "object") return "";
  const candidate = meta.relative_path || meta.source || meta.filename || "";
  const normalized = normalizePath(candidate);
  if (!normalized || normalized.includes("://")) return "";
  const stripped = stripDataFilesPrefix(normalized);
  return stripped.replace(/^\/+/, "");
};

const getDocFolderPath = (meta) => {
  const rel = getDocRelativePath(meta);
  if (!rel) return "";
  const parts = rel.split("/").filter(Boolean);
  if (parts.length <= 1) return "";
  return parts.slice(0, -1).join("/");
};

const getDocBaseName = (meta) => {
  const rel = getDocRelativePath(meta);
  if (rel) {
    const parts = rel.split("/").filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  const source = normalizePath(meta?.source || "");
  if (source && !source.includes("://")) {
    const parts = source.split("/").filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  return meta?.title || meta?.filename || "";
};

const getSafeUiUrl = (value) => {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("/") || /^https?:\/\//i.test(trimmed)) return trimmed;
  return "";
};

const getDocOpenUrl = (doc) => {
  if (!doc || typeof doc !== "object") return "";
  const meta = doc.meta && typeof doc.meta === "object" ? doc.meta : {};
  const directUrl = getSafeUiUrl(meta.url);
  if (directUrl) return directUrl;
  const sourceUrl = getSafeUiUrl(meta.source);
  if (sourceUrl) return sourceUrl;
  if (doc.id && classifyDocSource(meta).isFilesystem) {
    return `/api/knowledge/file/${encodeURIComponent(String(doc.id))}`;
  }
  return "";
};

const getDocPreviewKind = (doc) => {
  if (!doc || typeof doc !== "object") return "other";
  const meta = doc.meta && typeof doc.meta === "object" ? doc.meta : {};
  const candidates = [
    getDocOpenUrl(doc),
    meta.relative_path,
    meta.source,
    doc.baseName,
    meta.filename,
    meta.title,
  ];
  let ext = "";
  for (const candidate of candidates) {
    ext = getDocumentExtension(candidate);
    if (ext) break;
  }
  if (!ext) return "other";
  if (DOC_IMAGE_EXTENSIONS.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (DOC_VIDEO_EXTENSIONS.has(ext)) return "video";
  if (DOC_AUDIO_EXTENSIONS.has(ext)) return "audio";
  return "other";
};

const isTabularDoc = (doc) => {
  if (!doc || typeof doc !== "object") return false;
  const meta = doc.meta && typeof doc.meta === "object" ? doc.meta : {};
  const candidates = [
    getDocOpenUrl(doc),
    meta.relative_path,
    meta.source,
    doc.baseName,
    meta.filename,
    meta.title,
  ];
  return candidates.some((candidate) => TABULAR_DOC_EXTENSIONS.has(getDocumentExtension(candidate)));
};

const describeDocEditingProfile = (doc) => {
  if (!doc || typeof doc !== "object") {
    return {
      editable: false,
      formatLabel: "Document",
      editButtonLabel: "Edit text",
      saveButtonLabel: "Save text",
      helperText: "Inline editing is limited to plain text and markdown files.",
    };
  }
  const meta = doc.meta && typeof doc.meta === "object" ? doc.meta : {};
  const candidates = [
    getDocOpenUrl(doc),
    meta.relative_path,
    meta.source,
    doc.baseName,
    meta.filename,
    meta.title,
  ];
  let extension = "";
  for (const candidate of candidates) {
    extension = getDocumentExtension(candidate);
    if (extension) break;
  }
  const kind = String(meta.kind || meta.type || "").trim().toLowerCase();
  const isMarkdown =
    MARKDOWN_DOC_EXTENSIONS.has(extension) ||
    (!extension && kind === "markdown");
  const isEditableText =
    EDITABLE_TEXT_DOC_EXTENSIONS.has(extension) ||
    (!extension && TEXT_NOTE_DOC_KINDS.has(kind));
  if (isEditableText) {
    const isNote = !extension && kind === "note";
    return {
      editable: true,
      formatLabel: isMarkdown ? "Markdown document" : isNote ? "Plain text note" : "Plain text document",
      editButtonLabel: isMarkdown ? "Edit markdown" : "Edit text",
      saveButtonLabel: isMarkdown ? "Save markdown" : "Save text",
      helperText: isMarkdown
        ? "Inline editing is enabled for markdown files."
        : "Inline editing is enabled for plain text content.",
    };
  }
  if (isTabularDoc(doc)) {
    return {
      editable: false,
      formatLabel: "Tabular document",
      editButtonLabel: "Edit text",
      saveButtonLabel: "Save text",
      helperText: "Table-like files stay read-only here. Open the source file to edit them externally.",
    };
  }
  const previewKind = getDocPreviewKind(doc);
  if (previewKind === "pdf") {
    return {
      editable: false,
      formatLabel: "PDF document",
      editButtonLabel: "Edit text",
      saveButtonLabel: "Save text",
      helperText: "PDF files are view-only here. Open the source file to edit them externally.",
    };
  }
  if (previewKind === "image" || previewKind === "video" || previewKind === "audio") {
    return {
      editable: false,
      formatLabel: "Media document",
      editButtonLabel: "Edit text",
      saveButtonLabel: "Save text",
      helperText: "Media files are view-only here. Open the source file to edit them externally.",
    };
  }
  const extensionLabel = extension ? extension.toUpperCase() : "Document";
  return {
    editable: false,
    formatLabel: extension ? `${extensionLabel} file` : "Document",
    editButtonLabel: "Edit text",
    saveButtonLabel: "Save text",
    helperText: "Inline editing is limited to plain text and markdown files. Open the source file to edit this type.",
  };
};

const attachmentKeyOf = (attachment) => {
  if (!attachment || typeof attachment !== "object") return "";
  const key = attachment.content_hash || attachment.hash || attachment.filename || "";
  return String(key || "").trim();
};

const normalizeAttachmentFolderName = (value) => {
  if (typeof value !== "string") return "";
  return value.replace(/\s+/g, " ").trim().slice(0, 80);
};

const getAttachmentSourceFolderName = (attachment) => {
  if (!attachment || typeof attachment !== "object") return "";
  const sourceLabel =
    attachment.source_sync_label || attachment.source_sync_namespace || "";
  return normalizeAttachmentFolderName(String(sourceLabel || ""));
};

const classifyDocSource = (meta) => {
  const kind = String(meta?.kind || meta?.type || "").trim().toLowerCase();
  const source = normalizePath(meta?.source || "");
  const relativePath = getDocRelativePath(meta);
  const sourceLower = source.toLowerCase();
  const isMemory = kind === "memory" || sourceLower.startsWith("memory:");
  const isFilesystem = Boolean(relativePath) || appearsUnderDataFiles(source);
  const isExternal = !isMemory && !!source && isLikelyAbsolutePath(source) && !appearsUnderDataFiles(source);
  const isDerived =
    kind === "image_caption" || kind === "image_embedding" || meta?.derived === true;
  return {
    isMemory,
    isFilesystem,
    isExternal,
    isDerived,
  };
};

const normalizeFocusValue = (value) => {
  if (typeof value !== "string") return "";
  const stripped = stripDataFilesPrefix(value);
  return normalizePath(stripped).toLowerCase();
};

export const resolveFocusedDoc = (docs = [], focusId = "") => {
  const normalizedFocus = normalizeFocusValue(String(focusId || ""));
  if (!normalizedFocus) return null;
  const rows = Array.isArray(docs) ? docs : [];
  const exact = rows.find((doc) => {
    const meta = doc?.meta && typeof doc.meta === "object" ? doc.meta : {};
    const candidates = [
      String(doc?.id || ""),
      getDocRelativePath(meta),
      meta.source || "",
      meta.relative_path || "",
      getDocBaseName(meta),
      meta.title || "",
    ]
      .map((candidate) => normalizeFocusValue(String(candidate || "")))
      .filter(Boolean);
    return candidates.includes(normalizedFocus);
  });
  if (exact) return exact;
  return (
    rows.find((doc) => {
      const meta = doc?.meta && typeof doc.meta === "object" ? doc.meta : {};
      const relativePath = normalizeFocusValue(getDocRelativePath(meta));
      if (!relativePath) return false;
      return (
        relativePath.endsWith(`/${normalizedFocus}`) ||
        normalizedFocus.endsWith(`/${relativePath}`)
      );
    }) || null
  );
};

const DocumentsTab = ({ focusId = null }) => {
  const navigate = useNavigate();
  const [docs, setDocs] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState(DOC_SEARCH_MODES.CATALOG);
  const [sortBy, setSortBy] = useState("id");
  const [sortDir, setSortDir] = useState("asc");
  const [docViewMode, setDocViewMode] = useState("folders");
  const [collapsedDocFolders, setCollapsedDocFolders] = useState(() => new Set());
  const [showArchived, setShowArchived] = useState(false);
  const [showMemoryItems, setShowMemoryItems] = useState(false);
  const [showExternalItems, setShowExternalItems] = useState(false);
  const [showDerivedItems, setShowDerivedItems] = useState(false);
  const [actionStatus, setActionStatus] = useState("");
  const [activeActionModal, setActiveActionModal] = useState(null);
  const [docsInfoOpen, setDocsInfoOpen] = useState(false);

  const [uploadFile, setUploadFile] = useState(null);
  const [uploadPreview, setUploadPreview] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("");

  const [textSource, setTextSource] = useState("");
  const [textKind, setTextKind] = useState("note");
  const [textBody, setTextBody] = useState("");
  const [textStatus, setTextStatus] = useState("");

  const [ingestFolderPath, setIngestFolderPath] = useState("workspace");
  const [ingestRecursive, setIngestRecursive] = useState(true);
  const [ingestLimit, setIngestLimit] = useState("");
  const [ingestExtensions, setIngestExtensions] = useState("txt,md,pdf,csv,json");
  const [ingestBusy, setIngestBusy] = useState(false);
  const [ingestResult, setIngestResult] = useState(null);

  const [ragStatus, setRagStatus] = useState(null);

  const [semanticMode, setSemanticMode] = useState("hybrid");
  const [semanticLoading, setSemanticLoading] = useState(false);
  const [semanticError, setSemanticError] = useState("");
  const [semanticWarnings, setSemanticWarnings] = useState([]);
  const [semanticMatches, setSemanticMatches] = useState([]);
  const visibleSemanticMatches = useMemo(() => {
    if (showArchived) return semanticMatches;
    return semanticMatches.filter((match) => {
      const meta =
        match &&
        typeof match === "object" &&
        match.metadata &&
        typeof match.metadata === "object"
          ? match.metadata
          : {};
      return meta.archived !== true;
    });
  }, [semanticMatches, showArchived]);

  const [attachments, setAttachments] = useState([]);
  const [attachmentsLoading, setAttachmentsLoading] = useState(false);
  const [attachmentsError, setAttachmentsError] = useState("");
  const [attachmentQuery, setAttachmentQuery] = useState("");
  const [attachmentsIndexBusy, setAttachmentsIndexBusy] = useState(false);
  const [attachmentsIndexStatus, setAttachmentsIndexStatus] = useState(null);
  const [attachmentFolderAssignments, setAttachmentFolderAssignments] = useState(() => {
    if (typeof localStorage === "undefined") return {};
    try {
      const raw = localStorage.getItem(ATTACHMENT_FOLDER_ASSIGNMENTS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return {};
      const normalized = {};
      Object.entries(parsed).forEach(([key, folderName]) => {
        const safeKey = String(key || "").trim();
        const safeFolder = normalizeAttachmentFolderName(String(folderName || ""));
        if (!safeKey || !safeFolder) return;
        normalized[safeKey] = safeFolder;
      });
      return normalized;
    } catch {
      return {};
    }
  });
  const [attachmentFolderOrder, setAttachmentFolderOrder] = useState(() => {
    if (typeof localStorage === "undefined") return [];
    try {
      const raw = localStorage.getItem(ATTACHMENT_FOLDER_ORDER_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      const seen = new Set();
      return parsed
        .map((name) => normalizeAttachmentFolderName(String(name || "")))
        .filter((name) => {
          if (!name || seen.has(name)) return false;
          seen.add(name);
          return true;
        });
    } catch {
      return [];
    }
  });
  const [activeAttachmentFolder, setActiveAttachmentFolder] = useState(
    ATTACHMENT_FOLDER_ALL,
  );
  const [newAttachmentFolderName, setNewAttachmentFolderName] = useState("");
  const [attachmentDragHash, setAttachmentDragHash] = useState("");
  const [dragOverAttachmentFolder, setDragOverAttachmentFolder] = useState("");
  const [draggingFolderName, setDraggingFolderName] = useState("");
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [activeDoc, setActiveDoc] = useState(null);
  const [activeDocMode, setActiveDocMode] = useState("view");
  const [activeDocBody, setActiveDocBody] = useState("");
  const [activeDocLoading, setActiveDocLoading] = useState(false);
  const [activeDocSaving, setActiveDocSaving] = useState(false);
  const [activeDocError, setActiveDocError] = useState("");
  const docsInfoButtonRef = useRef(null);
  const docsInfoPanelRef = useRef(null);
  const focusedDocRef = useRef("");

  const clearUploadState = useCallback(() => {
    if (uploadPreview) {
      try {
        URL.revokeObjectURL(uploadPreview);
      } catch {
        // ignore object URL cleanup errors
      }
    }
    setUploadFile(null);
    setUploadPreview(null);
    setUploadStatus("");
  }, [uploadPreview]);

  const loadDocs = useCallback(async () => {
    try {
      const res = await axios.get("/api/knowledge/list");
      const ids = res.data?.ids || [];
      const metas = res.data?.metadatas || [];
      const list = ids.map((id, i) => ({ id, meta: metas[i] || {} }));
      setDocs(list);
    } catch {
      // ignore
    }
  }, []);

  const loadAttachments = useCallback(async () => {
    try {
      setAttachmentsLoading(true);
      setAttachmentsError("");
      const res = await axios.get("/api/attachments");
      setAttachments(res.data?.attachments || []);
    } catch (err) {
      console.error("load attachments failed", err);
      setAttachmentsError("Failed to load uploads");
    } finally {
      setAttachmentsLoading(false);
    }
  }, []);
  const loadRagStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/rag/status");
      setRagStatus(res.data || null);
    } catch {
      setRagStatus(null);
    }
  }, []);

  const loadDocBody = useCallback(async (doc, mode = "view") => {
    if (!doc?.id) return;
    const profile = describeDocEditingProfile(doc);
    const nextMode = mode === "edit" && profile.editable ? "edit" : "view";
    setActiveDoc(doc);
    setActiveDocMode(nextMode);
    setActiveDocError(mode === "edit" && !profile.editable ? profile.helperText : "");
    setActiveDocLoading(true);
    try {
      const res = await axios.get(`/api/knowledge/${encodeURIComponent(String(doc.id))}`);
      const text = String(res.data?.documents?.[0] || "");
      setActiveDocBody(text);
    } catch {
      setActiveDocBody("");
      setActiveDocError("Failed to load document content.");
    } finally {
      setActiveDocLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  useEffect(() => {
    loadAttachments();
  }, [loadAttachments]);

  useEffect(() => {
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(
        ATTACHMENT_FOLDER_ASSIGNMENTS_STORAGE_KEY,
        JSON.stringify(attachmentFolderAssignments),
      );
    } catch {
      // ignore persistence errors
    }
  }, [attachmentFolderAssignments]);

  useEffect(() => {
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(
        ATTACHMENT_FOLDER_ORDER_STORAGE_KEY,
        JSON.stringify(attachmentFolderOrder),
      );
    } catch {
      // ignore persistence errors
    }
  }, [attachmentFolderOrder]);

  useEffect(() => {
    const validKeys = new Set(attachments.map((att) => attachmentKeyOf(att)).filter(Boolean));
    setAttachmentFolderAssignments((prev) => {
      let changed = false;
      const next = {};
      Object.entries(prev).forEach(([key, folderName]) => {
        if (!validKeys.has(key)) {
          changed = true;
          return;
        }
        next[key] = folderName;
      });
      return changed ? next : prev;
    });
  }, [attachments]);

  useEffect(() => {
    if (!docsInfoOpen || ragStatus) return;
    loadRagStatus();
  }, [docsInfoOpen, loadRagStatus, ragStatus]);

  useEffect(() => {
    if (!docsInfoOpen) return undefined;
    const handlePointerDown = (event) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (docsInfoPanelRef.current?.contains(target)) return;
      if (docsInfoButtonRef.current?.contains(target)) return;
      setDocsInfoOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setDocsInfoOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [docsInfoOpen]);

  useEffect(() => {
    if (!focusId) return;
    setSearchMode(DOC_SEARCH_MODES.CATALOG);
    setSearchQuery(String(focusId));
  }, [focusId]);

  useEffect(
    () => () => {
      if (uploadPreview) {
        try {
          URL.revokeObjectURL(uploadPreview);
        } catch {
          // ignore
        }
      }
    },
    [uploadPreview],
  );

  const onUploadChange = (event) => {
    const selected = event.target.files?.[0];
    if (!selected) return;
    if (uploadPreview) {
      try {
        URL.revokeObjectURL(uploadPreview);
      } catch {
        // ignore
      }
    }
    setUploadFile(selected);
    setUploadPreview(URL.createObjectURL(selected));
    setUploadStatus("");
  };

  const uploadDocument = useCallback(async () => {
    if (!uploadFile) return;
    const formData = new FormData();
    formData.append("file", uploadFile);
    setUploadStatus("ingesting");
    try {
      await axios.post("/api/knowledge/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setUploadStatus("ingested");
      await loadDocs();
      setActionStatus("File ingested into knowledge.");
      clearUploadState();
      setActiveActionModal(null);
    } catch (err) {
      console.error("upload failed", err);
      setUploadStatus("error");
      setActionStatus("File ingest failed.");
    }
  }, [clearUploadState, loadDocs, uploadFile]);

  const saveTextDoc = useCallback(async () => {
    const body = textBody.trim();
    if (!body) {
      setTextStatus("missing");
      return;
    }
    setTextStatus("saving");
    try {
      await axios.post("/api/knowledge/text", {
        text: body,
        source: textSource.trim() || undefined,
        kind: textKind.trim() || undefined,
      });
      setTextStatus("saved");
      setTextBody("");
      setTextSource("");
      await loadDocs();
      setActionStatus("Quick note saved.");
      setActiveActionModal(null);
    } catch (err) {
      console.error("save text doc failed", err);
      setTextStatus("error");
    }
  }, [loadDocs, textBody, textKind, textSource]);

  const openKnowledge = useCallback(
    (match) => {
      const meta =
        match && typeof match === "object" && match.metadata && typeof match.metadata === "object"
          ? match.metadata
          : {};
      const memoryKey = meta.key || meta.memory_key;
      const eventId = meta.event_id;
      if (memoryKey) {
        navigate(`/knowledge?tab=memory&key=${encodeURIComponent(String(memoryKey))}`);
        return;
      }
      if (eventId) {
        navigate(`/knowledge?tab=calendar&event_id=${encodeURIComponent(String(eventId))}`);
        return;
      }
      if (match?.id) {
        navigate(`/knowledge?tab=documents&id=${encodeURIComponent(String(match.id))}`);
        return;
      }
      navigate("/knowledge?tab=documents");
    },
    [navigate],
  );

  const toggleExclude = useCallback(async (match) => {
    if (!match?.id && !match?.metadata) return;
    const meta =
      match.metadata && typeof match.metadata === "object" ? match.metadata : {};
    const memoryKey = meta.key || meta.memory_key;
    const nextValue = !(meta.rag_excluded || meta.excluded);
    try {
      if (memoryKey) {
        await axios.post(`/api/memory/${encodeURIComponent(String(memoryKey))}/exclude`, {
          value: nextValue,
        });
      } else if (match.id) {
        await axios.put(`/api/knowledge/${encodeURIComponent(String(match.id))}`, {
          metadata: { rag_excluded: nextValue },
        });
      }
    } catch (err) {
      console.error("exclude toggle failed", err);
    }
  }, []);

  const runSemanticSearch = useCallback(async (term, mode) => {
    const query = String(term || "").trim();
    if (!query) {
      setSemanticMatches([]);
      setSemanticWarnings([]);
      setSemanticError("");
      return;
    }
    setSemanticLoading(true);
    setSemanticError("");
    try {
      const res = await axios.get("/api/knowledge/query", {
        params: { q: query, k: 8, mode: mode || "hybrid" },
      });
      const matches = Array.isArray(res.data?.matches) ? res.data.matches : [];
      setSemanticMatches(matches.filter((m) => m && typeof m === "object"));
      setSemanticWarnings(Array.isArray(res.data?.warnings) ? res.data.warnings : []);
    } catch (err) {
      console.error("semantic search failed", err);
      setSemanticError("Search failed. Check backend logs.");
    } finally {
      setSemanticLoading(false);
    }
  }, []);

  useEffect(() => {
    if (searchMode !== DOC_SEARCH_MODES.SEMANTIC) {
      setSemanticMatches([]);
      setSemanticWarnings([]);
      setSemanticError("");
      setSemanticLoading(false);
      return;
    }
    const term = searchQuery.trim();
    if (!term) {
      setSemanticMatches([]);
      setSemanticWarnings([]);
      setSemanticError("");
      return;
    }
    const timer = window.setTimeout(() => {
      runSemanticSearch(term, semanticMode);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [runSemanticSearch, searchMode, searchQuery, semanticMode]);

  const reindexUploads = useCallback(async () => {
    setAttachmentsIndexBusy(true);
    setAttachmentsIndexStatus(null);
    try {
      const res = await axios.post("/api/attachments/rag/rehydrate", {
        dry_run: false,
      });
      setAttachmentsIndexStatus(res.data || null);
    } catch (err) {
      console.error("attachments reindex failed", err);
      setAttachmentsIndexStatus({ error: "rehydrate_failed" });
    } finally {
      setAttachmentsIndexBusy(false);
    }
  }, []);

  const openSystemUri = useCallback((uriValue) => {
    if (typeof uriValue !== "string" || !uriValue.trim()) return false;
    const uri = uriValue.trim();
    try {
      const win = window.open(uri, "_blank", "noopener,noreferrer");
      return Boolean(win);
    } catch {
      // Fall through to anchor click fallback.
    }
    try {
      const anchor = document.createElement("a");
      anchor.href = uri;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      return true;
    } catch {
      return false;
    }
  }, []);

  const revealKnowledgeDoc = useCallback(async (doc) => {
    if (!doc?.id) return;
    setActionStatus("");
    try {
      const res = await axios.get(`/api/knowledge/reveal/${encodeURIComponent(String(doc.id))}`);
      const payload = res.data || {};
      const path = typeof payload.path === "string" ? payload.path : "";
      const opened =
        payload.opened === true || openSystemUri(String(payload.open_uri || ""));
      setActionStatus(opened ? "Opened file location." : `Location: ${path}`);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setActionStatus(
        typeof detail === "string" && detail
          ? `Open location failed: ${detail}`
          : "Open location failed.",
      );
    }
  }, [openSystemUri]);

  const revealAttachment = useCallback(async (attachment) => {
    if (!attachment?.content_hash) return;
    setActionStatus("");
    try {
      const res = await axios.get(
        `/api/attachments/reveal/${encodeURIComponent(String(attachment.content_hash))}`,
        {
          params: attachment?.filename ? { filename: attachment.filename } : {},
        },
      );
      const payload = res.data || {};
      const path = typeof payload.path === "string" ? payload.path : "";
      const opened =
        payload.opened === true || openSystemUri(String(payload.open_uri || ""));
      setActionStatus(opened ? "Opened uploads location." : `Location: ${path}`);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setActionStatus(
        typeof detail === "string" && detail ? `Reveal failed: ${detail}` : "Reveal failed.",
      );
    }
  }, [openSystemUri]);

  const getAttachmentFolderFor = useCallback(
    (attachment) => {
      const key = attachmentKeyOf(attachment);
      if (key) {
        const folderName = normalizeAttachmentFolderName(
          String(attachmentFolderAssignments[key] || ""),
        );
        if (folderName) return folderName;
      }
      const sourceFolder = getAttachmentSourceFolderName(attachment);
      return sourceFolder || ATTACHMENT_FOLDER_UNSORTED;
    },
    [attachmentFolderAssignments],
  );

  const createAttachmentFolder = useCallback(() => {
    const folderName = normalizeAttachmentFolderName(newAttachmentFolderName);
    if (!folderName) return;
    setAttachmentFolderOrder((prev) => (prev.includes(folderName) ? prev : [...prev, folderName]));
    setActiveAttachmentFolder(folderName);
    setNewAttachmentFolderName("");
    setActionStatus(`Gallery folder ready: ${folderName}`);
  }, [newAttachmentFolderName]);

  const assignAttachmentToFolder = useCallback((attachmentKey, folderName) => {
    const safeKey = String(attachmentKey || "").trim();
    if (!safeKey) return;
    const normalizedFolder = normalizeAttachmentFolderName(String(folderName || ""));
    if (!normalizedFolder || normalizedFolder === ATTACHMENT_FOLDER_UNSORTED) {
      setAttachmentFolderAssignments((prev) => {
        if (!(safeKey in prev)) return prev;
        const next = { ...prev };
        delete next[safeKey];
        return next;
      });
      return;
    }
    setAttachmentFolderAssignments((prev) => {
      if (prev[safeKey] === normalizedFolder) return prev;
      return { ...prev, [safeKey]: normalizedFolder };
    });
    setAttachmentFolderOrder((prev) =>
      prev.includes(normalizedFolder) ? prev : [...prev, normalizedFolder],
    );
  }, []);

  const handleAttachmentDragStart = useCallback((event, attachment) => {
    const key = attachmentKeyOf(attachment);
    if (!key) return;
    setAttachmentDragHash(key);
    setDraggingFolderName("");
    if (event?.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", key);
    }
  }, []);

  const clearAttachmentDragState = useCallback(() => {
    setAttachmentDragHash("");
    setDragOverAttachmentFolder("");
  }, []);

  const handleFolderChipDragStart = useCallback((event, folderName) => {
    if (
      !folderName
      || folderName === ATTACHMENT_FOLDER_ALL
      || folderName === ATTACHMENT_FOLDER_UNSORTED
    ) {
      return;
    }
    setDraggingFolderName(folderName);
    setAttachmentDragHash("");
    if (event?.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", folderName);
    }
  }, []);

  const clearFolderDragState = useCallback(() => {
    setDraggingFolderName("");
    setDragOverAttachmentFolder("");
  }, []);

  const handleFolderDragOver = useCallback(
    (event, folderName) => {
      if (!attachmentDragHash && !draggingFolderName) return;
      event.preventDefault();
      if (dragOverAttachmentFolder !== folderName) {
        setDragOverAttachmentFolder(folderName);
      }
    },
    [attachmentDragHash, dragOverAttachmentFolder, draggingFolderName],
  );

  const handleFolderDragLeave = useCallback(
    (event, folderName) => {
      const nextTarget = event.relatedTarget;
      if (nextTarget && event.currentTarget?.contains(nextTarget)) return;
      if (dragOverAttachmentFolder === folderName) {
        setDragOverAttachmentFolder("");
      }
    },
    [dragOverAttachmentFolder],
  );

  const handleFolderDrop = useCallback(
    (event, folderName) => {
      event.preventDefault();
      const targetFolder =
        folderName === ATTACHMENT_FOLDER_ALL ? ATTACHMENT_FOLDER_UNSORTED : folderName;

      if (draggingFolderName) {
        const safeTarget = normalizeAttachmentFolderName(String(targetFolder || ""));
        if (
          safeTarget
          && safeTarget !== ATTACHMENT_FOLDER_UNSORTED
          && safeTarget !== draggingFolderName
        ) {
          setAttachmentFolderOrder((prev) => {
            const fromIndex = prev.indexOf(draggingFolderName);
            const toIndex = prev.indexOf(safeTarget);
            if (fromIndex < 0 || toIndex < 0) return prev;
            const next = [...prev];
            const [moved] = next.splice(fromIndex, 1);
            next.splice(toIndex, 0, moved);
            return next;
          });
        }
        clearFolderDragState();
        return;
      }

      const sourceKey = attachmentDragHash || event.dataTransfer?.getData("text/plain") || "";
      const safeKey = String(sourceKey || "").trim();
      if (!safeKey) {
        setDragOverAttachmentFolder("");
        return;
      }
      assignAttachmentToFolder(safeKey, targetFolder);
      clearAttachmentDragState();
    },
    [
      assignAttachmentToFolder,
      attachmentDragHash,
      clearAttachmentDragState,
      clearFolderDragState,
      draggingFolderName,
    ],
  );

  const openDocFile = useCallback(
    (doc) => {
      const url = getDocOpenUrl(doc);
      if (url) {
        window.open(url, "_blank", "noopener,noreferrer");
        setActionStatus("Opened file preview.");
        return;
      }
      revealKnowledgeDoc(doc);
    },
    [revealKnowledgeDoc],
  );

  const ingestFolder = useCallback(async () => {
    setIngestBusy(true);
    setIngestResult(null);
    setActionStatus("");
    try {
      const parsedLimit = Number.parseInt(ingestLimit, 10);
      const limit =
        Number.isFinite(parsedLimit) && parsedLimit > 0 ? parsedLimit : undefined;
      const extensions = ingestExtensions
        .split(",")
        .map((token) => token.trim())
        .filter(Boolean);
      const payload = {
        path: ingestFolderPath.trim() || "workspace",
        recursive: !!ingestRecursive,
      };
      if (limit) payload.limit = limit;
      if (extensions.length) payload.extensions = extensions;
      const res = await axios.post("/api/knowledge/ingest-folder", payload);
      setIngestResult(res.data || null);
      await loadDocs();
      setActionStatus("Folder ingest completed.");
      setActiveActionModal(null);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setActionStatus(
        typeof detail === "string" && detail
          ? `Folder ingest failed: ${detail}`
          : "Folder ingest failed.",
      );
    } finally {
      setIngestBusy(false);
    }
  }, [ingestExtensions, ingestFolderPath, ingestLimit, ingestRecursive, loadDocs]);

  const filteredDocs = useMemo(() => {
    let rows = docs.map((d) => {
      const meta = d?.meta && typeof d.meta === "object" ? d.meta : {};
      const folderPath = getDocFolderPath(meta);
      const baseName = getDocBaseName(meta);
      const sourceInfo = classifyDocSource(meta);
      return {
        ...d,
        meta,
        folderPath,
        baseName,
        ...sourceInfo,
      };
    });
    if (!showMemoryItems) rows = rows.filter((d) => !d.isMemory);
    if (!showExternalItems) rows = rows.filter((d) => !d.isExternal);
    if (!showDerivedItems) rows = rows.filter((d) => !d.isDerived);
    if (!showArchived) rows = rows.filter((d) => d?.meta?.archived !== true);

    const query =
      searchMode === DOC_SEARCH_MODES.CATALOG ? searchQuery.trim().toLowerCase() : "";
    if (query) {
      rows = rows.filter((d) => {
        const id = String(d.id || "").toLowerCase();
        const title = String(d.meta?.title || d.baseName || "").toLowerCase();
        const source = String(d.meta?.source || "").toLowerCase();
        const folder = String(d.folderPath || "").toLowerCase();
        return (
          id.includes(query) ||
          title.includes(query) ||
          source.includes(query) ||
          folder.includes(query)
        );
      });
    }
    const direction = sortDir === "asc" ? 1 : -1;
    const getter =
      sortBy === "title"
        ? (d) => String(d.meta?.title || d.baseName || "")
        : sortBy === "folder"
          ? (d) => String(d.folderPath || "")
          : (d) => String(d.id || "");
    rows = [...rows].sort((a, b) => {
      const av = getter(a);
      const bv = getter(b);
      const cmp = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
      if (cmp !== 0) return cmp * direction;
      return String(a.id || "").localeCompare(String(b.id || "")) * direction;
    });
    return rows;
  }, [
    docs,
    searchMode,
    searchQuery,
    showArchived,
    showDerivedItems,
    showExternalItems,
    showMemoryItems,
    sortBy,
    sortDir,
  ]);

  useEffect(() => {
    const focusKey = String(focusId || "").trim();
    if (!focusKey || !filteredDocs.length) return;
    const matchedDoc = resolveFocusedDoc(filteredDocs, focusKey);
    if (!matchedDoc?.id) return;
    if (focusedDocRef.current === `${focusKey}:${matchedDoc.id}`) return;
    focusedDocRef.current = `${focusKey}:${matchedDoc.id}`;
    loadDocBody(matchedDoc, "view");
  }, [filteredDocs, focusId, loadDocBody]);

  const hiddenCounts = useMemo(() => {
    let memory = 0;
    let external = 0;
    let derived = 0;
    docs.forEach((d) => {
      const meta = d?.meta && typeof d.meta === "object" ? d.meta : {};
      const flags = classifyDocSource(meta);
      if (flags.isMemory) memory += 1;
      if (flags.isExternal) external += 1;
      if (flags.isDerived) derived += 1;
    });
    return { memory, external, derived };
  }, [docs]);

  const folderTree = useMemo(() => {
    const createNode = (name, path) => ({
      name,
      path,
      documents: [],
      children: new Map(),
      sortKey: 0,
      totalCount: 0,
    });
    const root = createNode("", "");
    filteredDocs.forEach((doc, index) => {
      const segments = splitFolderPath(doc.folderPath);
      if (!segments.length) {
        root.documents.push({ doc, index });
        root.sortKey = Math.max(root.sortKey, index);
        return;
      }
      let node = root;
      segments.forEach((segment) => {
        const childPath = node.path ? `${node.path}/${segment}` : segment;
        if (!node.children.has(segment)) {
          node.children.set(segment, createNode(segment, childPath));
        }
        node = node.children.get(segment);
        node.sortKey = Math.max(node.sortKey, index);
      });
      node.documents.push({ doc, index });
    });

    const finalize = (node) => {
      node.documents.sort((a, b) => a.index - b.index);
      const children = Array.from(node.children.values());
      children.forEach(finalize);
      node.totalCount =
        node.documents.length +
        children.reduce((sum, child) => sum + (child.totalCount || 0), 0);
      const childMax = children.reduce(
        (max, child) => Math.max(max, child.sortKey || 0),
        0,
      );
      node.sortKey = Math.max(node.sortKey || 0, childMax);
      node.childList = children.sort(
        (a, b) => (b.sortKey || 0) - (a.sortKey || 0) || a.name.localeCompare(b.name),
      );
      return node;
    };

    return finalize(root);
  }, [filteredDocs]);

  const toggleDocFolder = useCallback((path) => {
    setCollapsedDocFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const closeDocInspector = useCallback(() => {
    if (activeDocSaving) return;
    setActiveDoc(null);
    setActiveDocBody("");
    setActiveDocError("");
    setActiveDocMode("view");
  }, [activeDocSaving]);

  const openDoc = useCallback(
    async (doc) => {
      await loadDocBody(doc, "view");
    },
    [loadDocBody],
  );

  const editDoc = useCallback(
    async (doc) => {
      await loadDocBody(doc, "edit");
    },
    [loadDocBody],
  );

  const saveActiveDoc = useCallback(async () => {
    if (!activeDoc?.id) return;
    const profile = describeDocEditingProfile(activeDoc);
    if (!profile.editable) {
      setActiveDocMode("view");
      setActiveDocError(profile.helperText);
      return;
    }
    setActiveDocSaving(true);
    setActiveDocError("");
    try {
      await axios.put(`/api/knowledge/${encodeURIComponent(String(activeDoc.id))}`, {
        text: activeDocBody,
      });
      await loadDocs();
      setActiveDocMode("view");
    } catch {
      setActiveDocError("Save failed. Check backend logs.");
    } finally {
      setActiveDocSaving(false);
    }
  }, [activeDoc, activeDocBody, loadDocs]);

  const deleteDoc = useCallback(
    async (doc) => {
      if (!doc?.id) return;
      if (!window.confirm("Delete document?")) return;
      try {
        await axios.delete(`/api/knowledge/${encodeURIComponent(String(doc.id))}`);
        loadDocs();
        if (activeDoc?.id === doc.id) closeDocInspector();
      } catch {
        alert("Delete failed.");
      }
    },
    [activeDoc?.id, closeDocInspector, loadDocs],
  );

  const sortedAttachments = useMemo(() => {
    return [...attachments].sort((a, b) => {
      const aTime = a.uploaded_at ? new Date(a.uploaded_at).getTime() : 0;
      const bTime = b.uploaded_at ? new Date(b.uploaded_at).getTime() : 0;
      if (aTime !== bTime) return bTime - aTime;
      return (b.size || 0) - (a.size || 0);
    });
  }, [attachments]);

  const attachmentFolders = useMemo(() => {
    const seen = new Set();
    const ordered = [];
    const pushFolder = (folderName) => {
      const safeFolder = normalizeAttachmentFolderName(String(folderName || ""));
      if (
        !safeFolder
        || safeFolder === ATTACHMENT_FOLDER_ALL
        || safeFolder === ATTACHMENT_FOLDER_UNSORTED
        || seen.has(safeFolder)
      ) {
        return;
      }
      seen.add(safeFolder);
      ordered.push(safeFolder);
    };
    attachmentFolderOrder.forEach((folderName) => {
      pushFolder(folderName);
    });
    Object.values(attachmentFolderAssignments).forEach((folderName) => {
      pushFolder(folderName);
    });
    sortedAttachments.forEach((attachment) => {
      pushFolder(getAttachmentSourceFolderName(attachment));
    });
    return ordered;
  }, [attachmentFolderAssignments, attachmentFolderOrder, sortedAttachments]);

  useEffect(() => {
    if (
      activeAttachmentFolder !== ATTACHMENT_FOLDER_ALL
      && activeAttachmentFolder !== ATTACHMENT_FOLDER_UNSORTED
      && !attachmentFolders.includes(activeAttachmentFolder)
    ) {
      setActiveAttachmentFolder(ATTACHMENT_FOLDER_ALL);
    }
  }, [activeAttachmentFolder, attachmentFolders]);

  const attachmentFolderCounts = useMemo(() => {
    const counts = {
      [ATTACHMENT_FOLDER_ALL]: sortedAttachments.length,
      [ATTACHMENT_FOLDER_UNSORTED]: 0,
    };
    attachmentFolders.forEach((folderName) => {
      counts[folderName] = 0;
    });
    sortedAttachments.forEach((att) => {
      const folderName = getAttachmentFolderFor(att);
      counts[folderName] = (counts[folderName] || 0) + 1;
    });
    return counts;
  }, [attachmentFolders, getAttachmentFolderFor, sortedAttachments]);

  const searchedAttachments = useMemo(() => {
    const term = attachmentQuery.trim().toLowerCase();
    if (!term) return sortedAttachments;
    return sortedAttachments.filter((att) => {
      const name = (att.filename || "").toLowerCase();
      const hash = (att.content_hash || "").toLowerCase();
      const sourceLabel = String(att.source_sync_label || att.source_sync_namespace || "").toLowerCase();
      const relativePath = String(att.relative_path || "").toLowerCase();
      return (
        name.includes(term)
        || hash.includes(term)
        || sourceLabel.includes(term)
        || relativePath.includes(term)
      );
    });
  }, [sortedAttachments, attachmentQuery]);

  const filteredAttachments = useMemo(() => {
    if (activeAttachmentFolder === ATTACHMENT_FOLDER_ALL) return searchedAttachments;
    return searchedAttachments.filter((att) => {
      const folderName = getAttachmentFolderFor(att);
      if (activeAttachmentFolder === ATTACHMENT_FOLDER_UNSORTED) {
        return folderName === ATTACHMENT_FOLDER_UNSORTED;
      }
      return folderName === activeAttachmentFolder;
    });
  }, [activeAttachmentFolder, getAttachmentFolderFor, searchedAttachments]);

  const attachmentViewerItems = useMemo(
    () => buildAttachmentViewerItems(filteredAttachments),
    [filteredAttachments],
  );

  const docCountLabel = useMemo(() => {
    const count = filteredDocs.length;
    if (!count) return "No documents in the current view.";
    return `${count} document${count === 1 ? "" : "s"} in current view`;
  }, [filteredDocs.length]);

  const ragUpdatedLabel = ragStatus?.last_modified
    ? formatTimestamp(ragStatus.last_modified)
    : null;
  const docsSummaryTooltip = useMemo(() => {
    const lines = ["Filesystem root: data/files", "Default folder: workspace"];
    if (hiddenCounts.memory > 0 && !showMemoryItems) {
      lines.push(`Hidden memory items: ${hiddenCounts.memory}`);
    }
    if (hiddenCounts.external > 0 && !showExternalItems) {
      lines.push(`Hidden external-path items: ${hiddenCounts.external}`);
    }
    if (hiddenCounts.derived > 0 && !showDerivedItems) {
      lines.push(`Hidden derived items: ${hiddenCounts.derived}`);
    }
    if (ragStatus?.backend) {
      lines.push(`Backend: ${ragStatus.backend}`);
    }
    if (ragStatus?.aux_models?.text_embeddings) {
      const textEmbeddings = ragStatus.aux_models.text_embeddings;
      lines.push(
        `Text embeddings: ${textEmbeddings.state || "unknown"} (${textEmbeddings.model || "default"})`,
      );
    }
    if (ragStatus?.aux_models?.clip_embeddings) {
      const clipEmbeddings = ragStatus.aux_models.clip_embeddings;
      lines.push(
        `CLIP embeddings: ${clipEmbeddings.state || "unknown"} (${clipEmbeddings.model || "default"})`,
      );
    }
    if (ragUpdatedLabel) {
      lines.push(`Updated: ${ragUpdatedLabel}`);
    }
    if (!ragStatus) {
      lines.push("Backend details load when you open this info panel.");
    }
    if (actionStatus) {
      lines.push(`Last action: ${actionStatus}`);
    }
    return lines.join("\n");
  }, [
    actionStatus,
    hiddenCounts.derived,
    hiddenCounts.external,
    hiddenCounts.memory,
    ragStatus,
    ragStatus?.backend,
    ragUpdatedLabel,
    showDerivedItems,
    showExternalItems,
    showMemoryItems,
  ]);
  const activeDocOpenUrl = useMemo(() => getDocOpenUrl(activeDoc), [activeDoc]);
  const activeDocPreviewKind = useMemo(() => getDocPreviewKind(activeDoc), [activeDoc]);
  const activeDocIsTabular = useMemo(() => isTabularDoc(activeDoc), [activeDoc]);
  const activeDocProfile = useMemo(() => describeDocEditingProfile(activeDoc), [activeDoc]);
  const canPreviewActiveDoc =
    Boolean(activeDocOpenUrl) && activeDocPreviewKind !== "other";

  const closeActionModal = () => {
    setActiveActionModal(null);
    setUploadStatus("");
    setTextStatus("");
    setIngestResult(null);
    clearUploadState();
  };

  const renderActionModalBody = () => {
    if (activeActionModal === "file") {
      return (
        <>
          <h3>Ingest file</h3>
          <div className="history-modal-body">
            <label className="history-modal-field">
              <span>File</span>
              <input type="file" onChange={onUploadChange} />
            </label>
            {uploadFile && (
              <div className="history-modal-helper">
                {uploadFile.name} ({formatBytes(uploadFile.size)})
              </div>
            )}
            {uploadPreview && (
              <div className="preview">
                <MediaViewer src={uploadPreview} showLink={false} file={uploadFile} />
              </div>
            )}
            {uploadStatus === "error" && (
              <div className="status-note warn">File ingest failed.</div>
            )}
          </div>
          <div className="history-modal-actions">
            <button type="button" onClick={closeActionModal}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={uploadDocument}
              disabled={!uploadFile || uploadStatus === "ingesting"}
            >
              {uploadStatus === "ingesting" ? "Ingesting..." : "Ingest"}
            </button>
          </div>
        </>
      );
    }
    if (activeActionModal === "folder") {
      return (
        <>
          <h3>Ingest folder</h3>
          <div className="history-modal-body">
            <label className="history-modal-field">
              <span>Folder path (under data/files)</span>
              <input
                type="text"
                value={ingestFolderPath}
                onChange={(event) => setIngestFolderPath(event.target.value)}
                placeholder="workspace"
              />
            </label>
            <label className="history-modal-field">
              <span>Extensions</span>
              <input
                type="text"
                value={ingestExtensions}
                onChange={(event) => setIngestExtensions(event.target.value)}
                placeholder="txt,md,pdf,csv,json"
              />
            </label>
            <label className="history-modal-field">
              <span>Limit (optional)</span>
              <input
                type="number"
                min="1"
                value={ingestLimit}
                onChange={(event) => setIngestLimit(event.target.value)}
                placeholder="0 = no limit"
              />
            </label>
            <label className="history-modal-field">
              <span>
                <input
                  type="checkbox"
                  checked={ingestRecursive}
                  onChange={(event) => setIngestRecursive(!!event.target.checked)}
                />{" "}
                recurse subfolders
              </span>
            </label>
            {ingestResult ? (
              <div className="status-note">
                ingested {ingestResult.count || 0}
                {typeof ingestResult.skipped?.length === "number"
                  ? ` | skipped ${ingestResult.skipped.length}`
                  : ""}
                {typeof ingestResult.errors?.length === "number"
                  ? ` | errors ${ingestResult.errors.length}`
                  : ""}
              </div>
            ) : null}
          </div>
          <div className="history-modal-actions">
            <button type="button" onClick={closeActionModal}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={ingestFolder}
              disabled={ingestBusy || !ingestFolderPath.trim()}
            >
              {ingestBusy ? "Ingesting..." : "Ingest"}
            </button>
          </div>
        </>
      );
    }
    if (activeActionModal === "note") {
      return (
        <>
          <h3>Quick note</h3>
          <div className="history-modal-body">
            <label className="history-modal-field">
              <span>Source / title</span>
              <input
                type="text"
                value={textSource}
                onChange={(event) => setTextSource(event.target.value)}
                placeholder="meeting-notes"
              />
            </label>
            <label className="history-modal-field">
              <span>Kind</span>
              <input
                type="text"
                value={textKind}
                onChange={(event) => setTextKind(event.target.value)}
                placeholder="note"
              />
            </label>
            <label className="history-modal-field">
              <span>Content</span>
              <textarea
                rows={5}
                value={textBody}
                onChange={(event) => setTextBody(event.target.value)}
                placeholder="Paste text to store and retrieve later."
              />
            </label>
            {textStatus === "missing" && (
              <div className="status-note warn">Enter text before saving.</div>
            )}
            {textStatus === "error" && (
              <div className="status-note warn">Save failed. Check logs.</div>
            )}
          </div>
          <div className="history-modal-actions">
            <button type="button" onClick={closeActionModal}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={saveTextDoc}
              disabled={textStatus === "saving" || !textBody.trim()}
            >
              {textStatus === "saving" ? "Saving..." : "Save"}
            </button>
          </div>
        </>
      );
    }
    return null;
  };

  return (
    <div className="documents-tab">
      <FilterBar
        searchPlaceholder={
          searchMode === DOC_SEARCH_MODES.SEMANTIC
            ? "Semantic search docs and captions"
            : "Search docs by id, title, source, or folder"
        }
        searchValue={searchQuery}
        onSearch={setSearchQuery}
      >
        <label>
          mode
          <select value={searchMode} onChange={(event) => setSearchMode(event.target.value)}>
            <option value={DOC_SEARCH_MODES.CATALOG}>catalog</option>
            <option value={DOC_SEARCH_MODES.SEMANTIC}>semantic</option>
          </select>
        </label>
        {searchMode === DOC_SEARCH_MODES.SEMANTIC && (
          <label>
            mode
            <select value={semanticMode} onChange={(event) => setSemanticMode(event.target.value)}>
              <option value="hybrid">hybrid (clip + text)</option>
              <option value="clip">clip (images)</option>
              <option value="text">text</option>
            </select>
          </label>
        )}
        <label>
          layout
          <select value={docViewMode} onChange={(event) => setDocViewMode(event.target.value)}>
            <option value="list">list</option>
            <option value="folders">folders</option>
          </select>
        </label>
        <label>
          sort
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
            <option value="id">id</option>
            <option value="title">title</option>
            <option value="folder">folder</option>
          </select>
        </label>
        <label>
          order
          <select value={sortDir} onChange={(event) => setSortDir(event.target.value)}>
            <option value="asc">asc</option>
            <option value="desc">desc</option>
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(!!event.target.checked)}
          />
          archived
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showMemoryItems}
            onChange={(event) => setShowMemoryItems(!!event.target.checked)}
          />
          memories
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showExternalItems}
            onChange={(event) => setShowExternalItems(!!event.target.checked)}
          />
          external paths
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showDerivedItems}
            onChange={(event) => setShowDerivedItems(!!event.target.checked)}
          />
          derived captions
        </label>
      </FilterBar>

      <section className="docs-summary">
        <div className="docs-summary-head">
          <p title={docsSummaryTooltip}>{docCountLabel}</p>
          <button
            ref={docsInfoButtonRef}
            type="button"
            className={`docs-summary-help${docsInfoOpen ? " is-open" : ""}`}
            title={docsSummaryTooltip}
            data-tooltip={docsSummaryTooltip}
            aria-expanded={docsInfoOpen}
            onClick={() => {
              setDocsInfoOpen((prev) => {
                const next = !prev;
                if (!prev && !ragStatus) {
                  loadRagStatus();
                }
                return next;
              });
            }}
          >
            info
          </button>
        </div>
        {docsInfoOpen ? (
          <div ref={docsInfoPanelRef} className="docs-summary-popover" role="note">
            <p>{docCountLabel}</p>
            {ragStatus ? (
              <p className="status-note">
                backend: {ragStatus.backend || "local"}
                {ragUpdatedLabel ? ` | updated ${ragUpdatedLabel}` : ""}
              </p>
            ) : (
              <p className="status-note">Loading backend details...</p>
            )}
            <p className="status-note" style={{ whiteSpace: "pre-line" }}>
              {docsSummaryTooltip}
            </p>
          </div>
        ) : null}
      </section>

      <section className="documents-actions-bar">
        <button
          type="button"
          onClick={() => setActiveActionModal("file")}
          title="Ingest a local file"
        >
          ingest file
        </button>
        <button
          type="button"
          onClick={() => setActiveActionModal("folder")}
          title="Ingest a folder from data/files"
        >
          ingest folder
        </button>
        <button
          type="button"
          onClick={() => setActiveActionModal("note")}
          title="Create a quick text note"
        >
          quick note
        </button>
      </section>

      {actionStatus ? <p className="status-note">{actionStatus}</p> : null}

      {searchMode === DOC_SEARCH_MODES.SEMANTIC && (
        <section className="text-ingest-panel" style={{ marginTop: 4 }}>
          <h3>Semantic results</h3>
          {semanticWarnings.length ? (
            <div className="status-note warn" role="note">
              {semanticWarnings.join(", ")}
            </div>
          ) : null}
          {semanticError ? <div className="status-note warn">{semanticError}</div> : null}
          {semanticLoading ? (
            <div className="status-note">Searching...</div>
          ) : searchQuery.trim() && visibleSemanticMatches.length === 0 ? (
            <div className="status-note">No matches.</div>
          ) : null}
          {visibleSemanticMatches.length ? (
            <table style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>score</th>
                  <th>source</th>
                  <th>kind</th>
                  <th>preview</th>
                  <th>actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleSemanticMatches.map((match) => {
                  const meta =
                    match.metadata && typeof match.metadata === "object" ? match.metadata : {};
                  const previewText =
                    typeof match.text === "string"
                      ? match.text.replace(/\s+/g, " ").trim()
                      : "";
                  const preview =
                    previewText.length > 180 ? `${previewText.slice(0, 177)}...` : previewText;
                  const kind = meta.kind || meta.type || "document";
                  const excluded = !!(meta.rag_excluded || meta.excluded);
                  const score =
                    typeof match.score === "number" && Number.isFinite(match.score)
                      ? match.score
                      : null;
                  const safeUrl =
                    typeof meta.url === "string" &&
                    (meta.url.startsWith("/") || /^https?:\/\//i.test(meta.url))
                      ? meta.url
                      : null;
                  return (
                    <tr key={match.id || meta.source || preview}>
                      <td>{score === null ? "-" : score.toFixed(3)}</td>
                      <td>{meta.source || match.id || "-"}</td>
                      <td>{kind}</td>
                      <td title={previewText}>{preview || "-"}</td>
                      <td>
                        <button type="button" onClick={() => openKnowledge(match)} title="View in Knowledge">
                          view
                        </button>
                        <button
                          type="button"
                          onClick={async () => {
                            await toggleExclude(match);
                            await runSemanticSearch(searchQuery, semanticMode);
                            loadDocs();
                          }}
                          title={
                            excluded
                              ? "Include: allow retrieval again"
                              : "Exclude: keep stored but omit from retrieval"
                          }
                        >
                          {excluded ? "include" : "exclude"}
                        </button>
                        {safeUrl && (
                          <a href={safeUrl} target="_blank" rel="noreferrer" style={{ marginLeft: 8 }}>
                            open file
                          </a>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : null}
        </section>
      )}

      {activeDoc ? (
        <section className="doc-inspector-panel" aria-live="polite">
          <div className="doc-inspector-header">
            <div className="doc-inspector-title-wrap">
              <h3>{activeDoc.meta?.title || activeDoc.baseName || activeDoc.id}</h3>
              <p>{activeDoc.meta?.source || activeDoc.baseName || activeDoc.id}</p>
              <div className="doc-inspector-format-row">
                <span
                  className={`doc-inspector-format-badge${activeDocProfile.editable ? " is-editable" : " is-read-only"}`}
                >
                  {activeDocProfile.formatLabel}
                </span>
                <span className="doc-inspector-format-help">{activeDocProfile.helperText}</span>
              </div>
            </div>
            <div className="doc-inspector-actions">
              {activeDocOpenUrl ? (
                <button
                  type="button"
                  onClick={() => openDocFile(activeDoc)}
                  title="Open file in a new tab"
                >
                  Open file
                </button>
              ) : null}
              {activeDoc?.isFilesystem ? (
                <button
                  type="button"
                  onClick={() => revealKnowledgeDoc(activeDoc)}
                  title="Reveal file in your OS file browser"
                >
                  Open location
                </button>
              ) : null}
              {activeDocProfile.editable && activeDocMode === "view" ? (
                <button
                  type="button"
                  onClick={() => setActiveDocMode("edit")}
                  title={activeDocProfile.editButtonLabel}
                >
                  {activeDocProfile.editButtonLabel}
                </button>
              ) : null}
              {activeDocProfile.editable && activeDocMode === "edit" ? (
                <>
                  <button
                    type="button"
                    onClick={saveActiveDoc}
                    disabled={activeDocSaving || activeDocLoading}
                    title={activeDocProfile.saveButtonLabel}
                  >
                    {activeDocSaving ? "Saving..." : activeDocProfile.saveButtonLabel}
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setActiveDocMode("view")}
                    disabled={activeDocSaving}
                  >
                    Cancel edit
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="ghost"
                onClick={closeDocInspector}
                disabled={activeDocSaving}
                title="Close inspector"
              >
                Close
              </button>
            </div>
          </div>
          {activeDocError ? <div className="status-note warn">{activeDocError}</div> : null}
          {activeDocLoading ? (
            <div className="status-note">Loading document...</div>
          ) : activeDocMode === "edit" ? (
            <textarea
              className="doc-inspector-editor"
              rows={10}
              value={activeDocBody}
              onChange={(event) => setActiveDocBody(event.target.value)}
            />
          ) : (
            <>
              {canPreviewActiveDoc ? (
                <div className="doc-inspector-preview">
                  <MediaViewer
                    src={activeDocOpenUrl}
                    alt={activeDoc.meta?.title || activeDoc.baseName || activeDoc.id}
                    showLink={false}
                  />
                </div>
              ) : null}
              {activeDocBody ? (
                activeDocIsTabular ? (
                  <CsvTablePreview text={activeDocBody} />
                ) : (
                  <pre className="doc-inspector-body">{activeDocBody}</pre>
                )
              ) : (
                <div className="status-note">
                  {canPreviewActiveDoc
                    ? "Preview available above. Text body is empty."
                    : "(empty document)"}
                </div>
              )}
            </>
          )}
        </section>
      ) : null}

      {filteredDocs.length ? (
        docViewMode === "folders" ? (
          <div className="documents-folder-tree">
            {renderFolderNode(folderTree, 0, true)}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>title</th>
                <th>source</th>
                <th>folder</th>
                <th>kind</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredDocs.map((d) => (
                <tr key={d.id}>
                  <td>{d.id}</td>
                  <td>{d.meta?.title || d.baseName || ""}</td>
                  <td>{d.meta?.source || "-"}</td>
                  <td>{d.folderPath || "-"}</td>
                  <td>
                    {d.meta?.kind
                      || (Array.isArray(d.meta?.tags)
                        ? d.meta.tags.join(", ")
                        : "-")}
                  </td>
                  <td>
                    <button type="button" onClick={() => openDoc(d)} title="Open document inspector">
                      inspect
                    </button>
                    {describeDocEditingProfile(d).editable ? (
                      <button type="button" onClick={() => editDoc(d)} title="Edit document text">
                        edit text
                      </button>
                    ) : null}
                    {getDocOpenUrl(d) ? (
                      <button type="button" onClick={() => openDocFile(d)} title="Open file in new tab">
                        open file
                      </button>
                    ) : null}
                    {d.isFilesystem ? (
                      <button
                        type="button"
                        onClick={() => revealKnowledgeDoc(d)}
                        title="Reveal file in your OS file browser"
                      >
                        open location
                      </button>
                    ) : null}
                    <button type="button" onClick={() => deleteDoc(d)} title="Delete document">
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : (
        <div className="status-note">No documents match this view.</div>
      )}

      <section className="attachments-panel">
        <div className="attachments-header">
          <h3>files</h3>
          <div className="attachments-controls">
            <input
              type="search"
              placeholder="Search uploads"
              value={attachmentQuery}
              onChange={(event) => setAttachmentQuery(event.target.value)}
            />
            <button
              type="button"
              onClick={loadAttachments}
              disabled={attachmentsLoading}
              title="Reload uploads list"
            >
              {attachmentsLoading ? "refreshing..." : "refresh"}
            </button>
            <button
              type="button"
              onClick={reindexUploads}
              disabled={attachmentsIndexBusy}
              title="(Re)caption + memorize existing image uploads into the knowledge base"
            >
              {attachmentsIndexBusy ? "memorizing..." : "memorize"}
            </button>
          </div>
        </div>
        {attachmentsIndexStatus ? (
          <div className="status-note">
            {typeof attachmentsIndexStatus.reindexed === "number"
              ? `memorized ${attachmentsIndexStatus.reindexed}`
              : attachmentsIndexStatus.error || "updated"}
          </div>
        ) : null}
        {attachmentsError && <div className="attachments-error">{attachmentsError}</div>}
        <div className="attachments-folders">
          <div className="attachments-folder-create">
            <input
              type="text"
              placeholder="New gallery folder"
              value={newAttachmentFolderName}
              onChange={(event) => setNewAttachmentFolderName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  createAttachmentFolder();
                }
              }}
            />
            <button
              type="button"
              onClick={createAttachmentFolder}
              title="Create a folder for grouping uploads"
            >
              add folder
            </button>
          </div>
          <div className="attachments-folder-chips">
            {[ATTACHMENT_FOLDER_ALL, ATTACHMENT_FOLDER_UNSORTED, ...attachmentFolders].map(
              (folderName) => {
                const isSpecial =
                  folderName === ATTACHMENT_FOLDER_ALL
                  || folderName === ATTACHMENT_FOLDER_UNSORTED;
                const isActive = activeAttachmentFolder === folderName;
                const isDragOver =
                  dragOverAttachmentFolder === folderName
                  && Boolean(attachmentDragHash || draggingFolderName);
                const label =
                  folderName === ATTACHMENT_FOLDER_ALL
                    ? "all"
                    : folderName === ATTACHMENT_FOLDER_UNSORTED
                      ? "unsorted"
                      : folderName;
                const count = attachmentFolderCounts[folderName] || 0;
                return (
                  <button
                    key={`attachment-folder-${folderName}`}
                    type="button"
                    className={`attachments-folder-chip${isActive ? " active" : ""}${isDragOver ? " drag-over" : ""}`}
                    onClick={() => setActiveAttachmentFolder(folderName)}
                    draggable={!isSpecial}
                    onDragStart={(event) => handleFolderChipDragStart(event, folderName)}
                    onDragEnd={clearFolderDragState}
                    onDragOver={(event) => handleFolderDragOver(event, folderName)}
                    onDragLeave={(event) => handleFolderDragLeave(event, folderName)}
                    onDrop={(event) => handleFolderDrop(event, folderName)}
                    title={
                      isSpecial
                        ? `Show ${label} uploads`
                        : "Drop images here to assign folder. Drag folder chips to reorder."
                    }
                  >
                    <span>{label}</span>
                    <span className="attachments-folder-count">{count}</span>
                  </button>
                );
              },
            )}
          </div>
        </div>
        {filteredAttachments.length ? (
          <div className="attachments-grid">
            {filteredAttachments.map((att, idx) => {
              const label = att.filename || att.content_hash;
              const assignmentKey = attachmentKeyOf(att);
              const key = assignmentKey || `att-${idx}`;
              const folderName = getAttachmentFolderFor(att);
              const folderLabel =
                folderName === ATTACHMENT_FOLDER_UNSORTED ? "unsorted" : folderName;
              const card = describeAttachmentCard(att, folderLabel);
              return (
                <div
                  key={key}
                  className={`attachments-card${attachmentDragHash === key ? " is-dragging" : ""}`}
                  draggable={Boolean(assignmentKey)}
                  onDragStart={(event) => handleAttachmentDragStart(event, att)}
                  onDragEnd={clearAttachmentDragState}
                >
                  <MediaViewer
                    src={att.url}
                    alt={label}
                    showLink={false}
                    contextItems={attachmentViewerItems}
                    contextIndex={idx}
                  />
                  <div className="attachment-meta">
                    <div className="attachment-topline">
                      <span className="attachment-name" title={card.label}>
                        {card.label}
                      </span>
                      <div className="attachment-topline-meta">
                        <div className="attachment-badges">
                          {card.badges.map((badge) => (
                            <span
                              key={badge.key}
                              className={badge.className}
                              title={badge.title}
                            >
                              {badge.label}
                            </span>
                          ))}
                        </div>
                        <div className="attachment-actions">
                          <button
                            type="button"
                            onClick={() => revealAttachment(att)}
                            title="Reveal upload location in your OS file browser. If blocked, location text appears above."
                          >
                            reveal
                          </button>
                        </div>
                      </div>
                    </div>
                    {card.captionText ? (
                      <div className="attachment-caption-line" title={card.captionText}>
                        <span className="attachment-caption-text">{card.captionText}</span>
                      </div>
                    ) : null}
                    {card.secondaryMeta.length ? (
                      <div className="attachment-secondary-line" title={card.secondaryMeta.join(" | ")}>
                        {card.secondaryMeta.join(" • ")}
                      </div>
                    ) : (
                      <div className="attachment-secondary-line attachment-secondary-line--empty" />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : !attachmentsLoading ? (
          <div className="attachments-empty">
            {attachmentQuery.trim() || activeAttachmentFolder !== ATTACHMENT_FOLDER_ALL
              ? "No uploads match this view."
              : "No uploads yet."}
          </div>
        ) : (
          <div className="attachments-loading">Loading uploads...</div>
        )}
      </section>

      {activeActionModal && (
        <div className="history-modal-overlay documents-modal-overlay" onClick={closeActionModal}>
          <div className="history-modal documents-action-modal" onClick={(event) => event.stopPropagation()}>
            {renderActionModalBody()}
          </div>
        </div>
      )}
    </div>
  );

  function renderDocRow(doc, depth) {
    const displayTitle = doc.meta?.title || doc.baseName || doc.id;
    const source = doc.meta?.source || doc.baseName || "-";
    const kind =
      doc.meta?.kind
        || (Array.isArray(doc.meta?.tags) ? doc.meta.tags.join(", ") : "-");
    return (
      <div
        key={`doc-${doc.id}`}
        className="documents-doc-row"
        style={depth ? { paddingLeft: depth * 14 } : undefined}
      >
        <div className="documents-doc-main">
          <button
            type="button"
            className="documents-doc-title"
            onClick={() => openDoc(doc)}
            title={displayTitle}
          >
            {displayTitle}
          </button>
          <span className="documents-doc-source" title={source}>
            {source}
          </span>
        </div>
        <div className="documents-doc-meta">
          <span className="documents-doc-kind">{kind}</span>
          <div className="documents-doc-actions">
            <button type="button" onClick={() => openDoc(doc)} title="Open document inspector">
              inspect
            </button>
            {describeDocEditingProfile(doc).editable ? (
              <button type="button" onClick={() => editDoc(doc)} title="Edit document text">
                edit text
              </button>
            ) : null}
            {getDocOpenUrl(doc) ? (
              <button type="button" onClick={() => openDocFile(doc)} title="Open file in new tab">
                open file
              </button>
            ) : null}
            {doc.isFilesystem ? (
              <button
                type="button"
                onClick={() => revealKnowledgeDoc(doc)}
                title="Reveal file in your OS file browser"
              >
                open location
              </button>
            ) : null}
            <button type="button" onClick={() => deleteDoc(doc)} title="Delete document">
              delete
            </button>
          </div>
        </div>
      </div>
    );
  }

  function renderFolderNode(node, depth, isRoot = false) {
    const key = isRoot ? "__root__" : node.path;
    const collapsed = collapsedDocFolders.has(key);
    const isTopLevelFolder = !isRoot && !node.path.includes("/");
    const shouldCollapseTopLevelScaffold =
      isTopLevelFolder &&
      node.documents.length === 0 &&
      (node.childList || []).length === 1;
    if (shouldCollapseTopLevelScaffold) {
      const [collapsedChild] = node.childList || [];
      return collapsedChild ? renderFolderNode(collapsedChild, depth) : null;
    }
    const childList = node.childList || [];
    const hasDocuments = node.documents.length > 0;
    const hasChildren = childList.length > 0;
    if (isRoot && !hasDocuments && !hasChildren) return null;
    const label = isRoot ? "Workspace root" : node.name;
    const count = isRoot ? node.documents.length : node.totalCount || node.documents.length;
    const shouldRenderRow = !isRoot || hasDocuments;
    const shouldShowChildren = !collapsed || !shouldRenderRow;
    return (
      <div key={`folder-${key || "root"}`} className="documents-folder-node">
        {shouldRenderRow ? (
          <button
            type="button"
            className={`documents-folder-row${collapsed ? " collapsed" : ""}`}
            style={{ paddingLeft: Math.max(depth * 14, 0) }}
            onClick={() => toggleDocFolder(key)}
            aria-expanded={!collapsed}
            title={!isRoot ? node.path : undefined}
          >
            <span className="documents-folder-caret" aria-hidden="true">
              {collapsed ? ">" : "v"}
            </span>
            <span className="documents-folder-name">{label}</span>
            <span className="documents-folder-count">{count}</span>
          </button>
        ) : null}
        {shouldShowChildren && (
          <div className="documents-folder-children">
            {hasDocuments ? node.documents.map(({ doc }) => renderDocRow(doc, depth + 1)) : null}
            {childList.map((child) => renderFolderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  }
};

export default DocumentsTab;
