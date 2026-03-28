import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import "../styles/MediaViewer.css";

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "svg", "webp"];
const VIDEO_EXTENSIONS = ["mp4", "webm"];
const AUDIO_EXTENSIONS = ["mp3", "wav"];
const ZOOM_MIN = 0.8;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.2;
const VIEWPORT_BOOST_FACTOR = 1.3;
const VIEWPORT_WIDE_THRESHOLD = 1.85;
const VIEWPORT_TALL_THRESHOLD = 0.68;
const BASE_SURFACE_MAX_WIDTH_PX = 1320;
const BASE_WRAPPER_HEIGHT_VH = 64;
const MAX_WRAPPER_HEIGHT_VH = 84;

const cleanExtension = (value) => {
  if (typeof value !== "string") return "";
  const clean = value.split("?")[0].split("#")[0];
  const dot = clean.lastIndexOf(".");
  if (dot === -1) return "";
  return clean.slice(dot + 1).toLowerCase();
};

const classifyMedia = (extension) => {
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  if (extension === "pdf") return "pdf";
  if (VIDEO_EXTENSIONS.includes(extension)) return "video";
  if (AUDIO_EXTENSIONS.includes(extension)) return "audio";
  return "other";
};

const extractContentHash = (value) => {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, window.location.origin);
    const parts = url.pathname.split("/").filter(Boolean);
    const idx = parts.indexOf("attachments");
    if (idx !== -1 && parts.length > idx + 1) {
      try {
        return decodeURIComponent(parts[idx + 1]);
      } catch {
        return parts[idx + 1];
      }
    }
  } catch {
    const clean = value.split("?")[0].split("#")[0];
    const parts = clean.split("/").filter(Boolean);
    const idx = parts.indexOf("attachments");
    if (idx !== -1 && parts.length > idx + 1) {
      try {
        return decodeURIComponent(parts[idx + 1]);
      } catch {
        return parts[idx + 1];
      }
    }
  }
  return null;
};

const normalizeItems = (items, fallback) => {
  if (!Array.isArray(items) || !items.length) {
    return fallback;
  }
  const normalized = items
    .map((item) => {
      if (!item || !item.src) return null;
      return {
        src: item.src,
        alt: item.alt || "",
        file: item.file || null,
        label: item.label || item.alt || "",
        caption: item.caption || "",
        size:
          typeof item.size === "number" && Number.isFinite(item.size)
            ? item.size
            : null,
        uploadedAt: item.uploadedAt || item.uploaded_at || null,
        origin: item.origin || null,
        relativePath: item.relativePath || item.relative_path || null,
        captureSource: item.captureSource || item.capture_source || null,
        captionStatus: item.captionStatus || item.caption_status || null,
        indexStatus: item.indexStatus || item.index_status || null,
        indexWarning: item.indexWarning || item.index_warning || null,
        placeholderCaption:
          typeof item.placeholderCaption === "boolean"
            ? item.placeholderCaption
            : typeof item.placeholder_caption === "boolean"
              ? item.placeholder_caption
              : null,
        contentHash:
          (typeof item.contentHash === "string" && item.contentHash.trim()) ||
          (typeof item.content_hash === "string" && item.content_hash.trim()) ||
          null,
      };
    })
    .filter(Boolean);
  return normalized.length ? normalized : fallback;
};

const fileNameFromMedia = (src, alt) =>
  alt || (typeof src === "string" ? src.split("/").pop() : "image") || "image";

const fileNameFromSrc = (src) => {
  if (typeof src !== "string" || !src.trim()) return "";
  const clean = src.split("?")[0].split("#")[0];
  const raw = clean.split("/").filter(Boolean).pop() || "";
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

const formatBytes = (value) => {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) return "";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
};

const formatUploadedAt = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
};

const MediaViewer = ({
  src,
  alt = "",
  showLink = true,
  file = null,
  contextItems = null,
  contextIndex = 0,
}) => {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [revealBusy, setRevealBusy] = useState(false);
  const [revealError, setRevealError] = useState("");
  const [revealInfo, setRevealInfo] = useState(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [captionLoading, setCaptionLoading] = useState(false);
  const [captionGenerating, setCaptionGenerating] = useState(false);
  const [captionSaving, setCaptionSaving] = useState(false);
  const [captionDeleting, setCaptionDeleting] = useState(false);
  const [captionError, setCaptionError] = useState("");
  const [storedCaption, setStoredCaption] = useState("");
  const [captionDraft, setCaptionDraft] = useState("");
  const [captionEditOpen, setCaptionEditOpen] = useState(false);
  const [captionModelInfo, setCaptionModelInfo] = useState(null);
  const [captionModelInfoBusy, setCaptionModelInfoBusy] = useState(false);
  const [captionModelInfoError, setCaptionModelInfoError] = useState("");
  const [mediaNaturalSize, setMediaNaturalSize] = useState({ width: 0, height: 0 });
  const [viewportBoost, setViewportBoost] = useState({ width: 1, height: 1 });
  const panStartRef = useRef(null);
  const dialogRef = useRef(null);
  const mediaWrapperRef = useRef(null);
  const activeMediaRef = useRef(null);

  const fallbackItem = useMemo(
    () => [{ src, alt, file, label: alt || fileNameFromMedia(src, alt) }],
    [src, alt, file],
  );
  const viewerItems = useMemo(
    () => normalizeItems(contextItems, fallbackItem),
    [contextItems, fallbackItem],
  );

  useEffect(() => {
    if (!viewerItems.length && activeIndex !== 0) {
      setActiveIndex(0);
      return;
    }
    if (viewerItems.length && activeIndex >= viewerItems.length) {
      setActiveIndex(viewerItems.length - 1);
    }
  }, [viewerItems, activeIndex]);

  const computeIndex = useCallback(
    (items) => {
      if (!Array.isArray(items) || !items.length) return 0;
      const direct = items.findIndex((item) => item.src === src);
      if (direct !== -1) return direct;
      if (Array.isArray(contextItems) && contextItems.length) {
        const bounded = Math.min(Math.max(contextIndex || 0, 0), items.length - 1);
        return bounded;
      }
      return 0;
    },
    [contextIndex, contextItems, src],
  );

  const resetDialogState = () => {
    setError("");
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setIsPanning(false);
    panStartRef.current = null;
    setRevealBusy(false);
    setRevealError("");
    setRevealInfo(null);
    setCaptionLoading(false);
    setCaptionGenerating(false);
    setCaptionSaving(false);
    setCaptionDeleting(false);
    setCaptionError("");
    setStoredCaption("");
    setCaptionDraft("");
    setCaptionEditOpen(false);
    setCaptionModelInfo(null);
    setCaptionModelInfoBusy(false);
    setCaptionModelInfoError("");
    setMediaNaturalSize({ width: 0, height: 0 });
    setViewportBoost({ width: 1, height: 1 });
  };

  const openViewer = () => {
    setActiveIndex(computeIndex(viewerItems));
    resetDialogState();
    setOpen(true);
  };

  const closeViewer = () => setOpen(false);

  const handlePreviewKey = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openViewer();
    }
  };

  const hasCarousel = viewerItems.length > 1;
  const activeItem = viewerItems[activeIndex] || viewerItems[0] || fallbackItem[0];
  const activeSrc = activeItem?.src || src;
  const activeAlt = activeItem?.alt || alt;
  const activeFile = activeItem?.file || file;
  const activeExtension = cleanExtension(activeSrc);
  const activeKind = classifyMedia(activeExtension);
  const isZoomable = activeKind === "image" || activeKind === "pdf";
  const isZoomed = zoom > 1.01;
  const activeHash =
    (typeof activeItem?.contentHash === "string" && activeItem.contentHash.trim()) ||
    extractContentHash(activeSrc);
  const activeMemoryKey = activeHash ? `image:${activeHash}` : "";
  const activeName =
    activeItem?.label ||
    activeAlt ||
    fileNameFromMedia(activeSrc, activeAlt) ||
    "file";
  const activeAttachmentFilename =
    fileNameFromSrc(activeSrc) ||
    (activeFile && typeof activeFile.name === "string" ? activeFile.name : "") ||
    fileNameFromMedia(activeSrc, activeAlt);
  const activeSize =
    typeof activeItem?.size === "number" && Number.isFinite(activeItem.size)
      ? activeItem.size
      : activeFile && typeof activeFile.size === "number" && Number.isFinite(activeFile.size)
        ? activeFile.size
        : null;
  const activeUploadedAt = activeItem?.uploadedAt || null;
  const activeOrigin = activeItem?.origin || "";
  const activeRelativePath = activeItem?.relativePath || "";
  const activeCaptureSource = activeItem?.captureSource || "";
  const activeContextCaption = activeItem?.caption || "";
  const activeCaptionStatus = activeItem?.captionStatus || "";
  const activeIndexStatus = activeItem?.indexStatus || "";
  const activeIndexWarning = activeItem?.indexWarning || "";
  const activePlaceholderCaption = activeItem?.placeholderCaption === true;
  const activeStatusDetails = [
    activeOrigin ? `origin: ${activeOrigin}` : "",
    activeIndexStatus ? `index: ${activeIndexStatus}` : "",
    activeIndexWarning ? `index warning: ${activeIndexWarning}` : "",
    activeCaptureSource ? `capture: ${activeCaptureSource}` : "",
    activeRelativePath ? `path: ${activeRelativePath}` : "",
  ].filter(Boolean);
  const displayCaption = storedCaption || activeContextCaption;

  const getZoomedMediaBounds = useCallback(
    (zoomValue = zoom) => {
      const nextZoom = Number.isFinite(zoomValue) ? Math.max(zoomValue, 0.01) : 1;
      const wrapper = mediaWrapperRef.current;
      if (!(wrapper instanceof HTMLElement)) return null;
      const wrapperRect = wrapper.getBoundingClientRect();
      const wrapperWidth = wrapper.clientWidth || wrapperRect.width;
      const wrapperHeight = wrapper.clientHeight || wrapperRect.height;
      if (wrapperWidth <= 0 || wrapperHeight <= 0) return null;

      if (activeKind === "image") {
        const media = activeMediaRef.current;
        const naturalWidth =
          media instanceof HTMLImageElement && media.naturalWidth > 0
            ? media.naturalWidth
            : mediaNaturalSize.width;
        const naturalHeight =
          media instanceof HTMLImageElement && media.naturalHeight > 0
            ? media.naturalHeight
            : mediaNaturalSize.height;
        if (naturalWidth > 0 && naturalHeight > 0) {
          const fitScale = Math.min(wrapperWidth / naturalWidth, wrapperHeight / naturalHeight);
          return {
            wrapperWidth,
            wrapperHeight,
            mediaWidth: naturalWidth * fitScale * nextZoom,
            mediaHeight: naturalHeight * fitScale * nextZoom,
          };
        }
      }

      const media = activeMediaRef.current;
      if (media instanceof HTMLElement) {
        const mediaRect = media.getBoundingClientRect();
        if (mediaRect.width > 0 && mediaRect.height > 0) {
          const currentZoom = Math.max(zoom, 0.01);
          const baseWidth = mediaRect.width / currentZoom;
          const baseHeight = mediaRect.height / currentZoom;
          return {
            wrapperWidth,
            wrapperHeight,
            mediaWidth: baseWidth * nextZoom,
            mediaHeight: baseHeight * nextZoom,
          };
        }
      }
      return null;
    },
    [activeKind, mediaNaturalSize.height, mediaNaturalSize.width, zoom],
  );

  const clampPan = useCallback(
    (nextPan, zoomValue = zoom) => {
      const nextZoom = Number.isFinite(zoomValue) ? zoomValue : 1;
      if (nextZoom <= 1.01) {
        return { x: 0, y: 0 };
      }
      const bounds = getZoomedMediaBounds(nextZoom);
      if (bounds) {
        const limitX = Math.max(0, (bounds.mediaWidth - bounds.wrapperWidth) / 2);
        const limitY = Math.max(0, (bounds.mediaHeight - bounds.wrapperHeight) / 2);
        return {
          x: Math.max(-limitX, Math.min(limitX, nextPan.x)),
          y: Math.max(-limitY, Math.min(limitY, nextPan.y)),
        };
      }
      const fallbackLimit = Math.max(0, (nextZoom - 1) * 420);
      return {
        x: Math.max(-fallbackLimit, Math.min(fallbackLimit, nextPan.x)),
        y: Math.max(-fallbackLimit, Math.min(fallbackLimit, nextPan.y)),
      };
    },
    [getZoomedMediaBounds, zoom],
  );

  const setZoomValue = useCallback(
    (value) => {
      setZoom(() => {
        const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
        setPan((current) => clampPan(current, next));
        return Number(next.toFixed(2));
      });
    },
    [clampPan],
  );

  const panByWheel = useCallback(
    (dx, dy) => {
      setPan((current) =>
        clampPan({
          x: current.x + dx,
          y: current.y + dy,
        }),
      );
    },
    [clampPan],
  );

  const adjustZoom = useCallback(
    (delta) => {
      setZoom((prev) => {
        const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, prev + delta));
        setPan((current) => clampPan(current, next));
        return Number(next.toFixed(2));
      });
    },
    [clampPan],
  );

  const resetZoom = () => {
    setZoomValue(1);
    setPan({ x: 0, y: 0 });
  };

  useEffect(() => {
    if (!open) {
      setIsPanning(false);
      panStartRef.current = null;
      return;
    }
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setIsPanning(false);
    panStartRef.current = null;
    setMediaNaturalSize({ width: 0, height: 0 });
    setViewportBoost({ width: 1, height: 1 });
  }, [activeIndex, open]);

  useEffect(() => {
    if (!open || !isZoomable) return undefined;
    const rafId = window.requestAnimationFrame(() => {
      setPan((current) => clampPan(current, zoom));
    });
    const handleResize = () => {
      setPan((current) => clampPan(current, zoom));
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.cancelAnimationFrame(rafId);
      window.removeEventListener("resize", handleResize);
    };
  }, [clampPan, isZoomable, open, zoom]);

  useEffect(() => {
    if (!open || activeKind === "image") return;
    setViewportBoost({ width: 1, height: 1 });
  }, [activeKind, open]);

  useEffect(() => {
    if (!open) return undefined;
    const previousBody = document.body.style.overflow;
    const previousHtml = document.documentElement.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousBody;
      document.documentElement.style.overflow = previousHtml;
    };
  }, [open]);

  useEffect(() => {
    if (!open || !isZoomable) return undefined;
    const handleWheelCapture = (event) => {
      if (!event.ctrlKey) return;
      const dialog = dialogRef.current;
      const target = event?.target;
      if (!dialog || !(target instanceof Node) || !dialog.contains(target)) return;
      event.preventDefault();
    };
    window.addEventListener("wheel", handleWheelCapture, {
      passive: false,
      capture: true,
    });
    return () => {
      window.removeEventListener("wheel", handleWheelCapture, true);
    };
  }, [isZoomable, open]);

  useEffect(() => {
    if (!open || !activeHash) {
      setStoredCaption("");
      setCaptionDraft("");
      setCaptionEditOpen(false);
      setCaptionLoading(false);
      setCaptionError("");
      return;
    }
    let active = true;
    setCaptionLoading(true);
    setCaptionError("");
    axios
      .get(`/api/attachments/caption/${encodeURIComponent(activeHash)}`)
      .then((res) => {
        if (!active) return;
        const caption = String(res.data?.caption || "").trim();
        setStoredCaption(caption);
        setCaptionDraft(caption);
      })
      .catch(() => {
        if (!active) return;
        setStoredCaption("");
        setCaptionDraft("");
      })
      .finally(() => {
        if (!active) return;
        setCaptionLoading(false);
      });
    return () => {
      active = false;
    };
  }, [activeHash, open]);

  const handleViewerWheel = (event) => {
    if (!isZoomable) return;
    if (event.ctrlKey) {
      event.preventDefault();
      event.stopPropagation();
      const delta = event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
      adjustZoom(delta);
      return;
    }
    if (zoom <= 1.01) return;
    const horizontalDelta = event.shiftKey ? event.deltaY : event.deltaX;
    const verticalDelta = event.shiftKey ? 0 : event.deltaY;
    if (!horizontalDelta && !verticalDelta) return;
    event.preventDefault();
    event.stopPropagation();
    panByWheel(-horizontalDelta * 0.45, -verticalDelta * 0.45);
  };

  const beginPan = (event) => {
    if (!isZoomable || zoom <= 1.01) return;
    if (event.button !== 0) return;
    event.preventDefault();
    panStartRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: pan.x,
      panY: pan.y,
    };
    setIsPanning(true);
    if (typeof event.currentTarget.setPointerCapture === "function") {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
  };

  const movePan = (event) => {
    if (!isPanning || !panStartRef.current) return;
    event.preventDefault();
    const next = {
      x: panStartRef.current.panX + (event.clientX - panStartRef.current.x),
      y: panStartRef.current.panY + (event.clientY - panStartRef.current.y),
    };
    setPan(clampPan(next));
  };

  const endPan = (event) => {
    if (!isPanning) return;
    setIsPanning(false);
    panStartRef.current = null;
    if (
      event &&
      event.currentTarget &&
      typeof event.currentTarget.releasePointerCapture === "function"
    ) {
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may already be released; ignore.
      }
    }
  };

  const loadActiveBlob = useCallback(async () => {
    if (activeFile instanceof File) {
      return activeFile;
    }
    const resp = await fetch(activeSrc);
    if (!resp.ok) {
      throw new Error(`Failed to fetch media (${resp.status})`);
    }
    return await resp.blob();
  }, [activeFile, activeSrc]);

  const generateCaption = async () => {
    if (activeKind !== "image") return;
    try {
      setCaptionGenerating(true);
      setCaptionError("");
      const blob = await loadActiveBlob();
      const name = fileNameFromMedia(activeSrc, activeAlt);
      const form = new FormData();
      form.append("file", blob, name);
      const response = await axios.post("/api/knowledge/caption-image", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const caption = String(response.data?.caption || "").trim();
      const captionModel = String(response.data?.caption_model || "").trim();
      const clipModel = String(response.data?.clip?.model || "").trim();
      if (captionModel || clipModel) {
        setCaptionModelInfo((prev) => ({
          visionModel: captionModel || prev?.visionModel || "(unset)",
          ragEmbeddingModel: prev?.ragEmbeddingModel || "(unknown)",
          ragClipModel: clipModel || prev?.ragClipModel || "(unset)",
        }));
      }
      if (activeHash && caption) {
        try {
          await axios.put(`/api/attachments/caption/${encodeURIComponent(activeHash)}`, {
            caption,
          });
          setStoredCaption(caption);
        } catch {
          // Keep generated caption in the editor even if metadata save fails.
        }
      }
      setCaptionDraft(caption);
      setCaptionEditOpen(true);
    } catch (err) {
      console.error("caption generate failed", err);
      setCaptionError("Caption indexing failed.");
    } finally {
      setCaptionGenerating(false);
    }
  };

  const saveCaption = async () => {
    const nextCaption = String(captionDraft || "").trim();
    if (!nextCaption) {
      setCaptionError("Caption cannot be empty.");
      return;
    }
    if (!activeHash) {
      setCaptionError("Saving captions is only supported for uploaded attachments.");
      return;
    }
    try {
      setCaptionSaving(true);
      setCaptionError("");
      await axios.put(`/api/attachments/caption/${encodeURIComponent(activeHash)}`, {
        caption: nextCaption,
      });
      setStoredCaption(nextCaption);
      setCaptionDraft(nextCaption);
      setCaptionEditOpen(false);
    } catch (err) {
      console.error("caption save failed", err);
      setCaptionError("Caption save failed.");
    } finally {
      setCaptionSaving(false);
    }
  };

  const deleteCaption = async () => {
    if (!activeHash) {
      setStoredCaption("");
      setCaptionDraft("");
      setCaptionEditOpen(false);
      return;
    }
    try {
      setCaptionDeleting(true);
      setCaptionError("");
      await axios.delete(`/api/attachments/caption/${encodeURIComponent(activeHash)}`);
      setStoredCaption("");
      setCaptionDraft("");
      setCaptionEditOpen(false);
    } catch (err) {
      console.error("caption delete failed", err);
      setCaptionError("Caption delete failed.");
    } finally {
      setCaptionDeleting(false);
    }
  };

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

  const toFileUri = useCallback((pathValue) => {
    if (typeof pathValue !== "string") return "";
    const raw = pathValue.trim();
    if (!raw) return "";
    if (/^file:/i.test(raw)) return raw;
    if (/^[a-zA-Z]:[\\/]/.test(raw)) {
      const normalized = raw.replace(/\\/g, "/").replace(/^\/+/, "");
      return `file:///${encodeURI(normalized)}`;
    }
    if (raw.startsWith("\\\\")) {
      const normalized = raw.replace(/\\/g, "/");
      return `file:${encodeURI(normalized)}`;
    }
    return "";
  }, []);

  const handleViewSavedMemory = useCallback(() => {
    if (!activeMemoryKey) return;
    setOpen(false);
    navigate(`/knowledge?tab=memory&key=${encodeURIComponent(activeMemoryKey)}`);
  }, [activeMemoryKey, navigate]);

  const loadCaptionModelInfo = useCallback(async () => {
    try {
      setCaptionModelInfoBusy(true);
      setCaptionModelInfoError("");
      const res = await axios.get("/api/settings");
      const payload = res?.data || {};
      setCaptionModelInfo({
        visionModel: String(payload.vision_model || "").trim() || "(unset)",
        ragEmbeddingModel:
          String(payload.rag_embedding_model || "").trim() || "(unset)",
        ragClipModel:
          String(payload.rag_clip_model || payload.vision_model || "").trim() || "(unset)",
      });
    } catch {
      setCaptionModelInfoError("Model details are unavailable right now.");
    } finally {
      setCaptionModelInfoBusy(false);
    }
  }, []);

  const handleReveal = async () => {
    if (!activeHash) {
      setRevealError("No attachment hash found for this file.");
      return;
    }
    try {
      setRevealBusy(true);
      setRevealError("");
      const res = await axios.get(
        `/api/attachments/reveal/${encodeURIComponent(activeHash)}`,
        {
          params: activeAttachmentFilename ? { filename: activeAttachmentFilename } : {},
        },
      );
      const payload = res.data || {};
      const fallbackUri = toFileUri(String(payload.folder || payload.path || ""));
      const opened = payload.opened === true
        || openSystemUri(String(payload.open_uri || ""))
        || openSystemUri(fallbackUri);
      setRevealInfo({
        ...payload,
        opened,
      });
    } catch (err) {
      console.error("open in folder failed", err);
      const detail =
        typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "";
      setRevealError(detail || "Open folder failed.");
    } finally {
      setRevealBusy(false);
    }
  };

  const goPrev = () => {
    if (!hasCarousel) return;
    setActiveIndex((prev) => (prev - 1 + viewerItems.length) % viewerItems.length);
  };

  const goNext = () => {
    if (!hasCarousel) return;
    setActiveIndex((prev) => (prev + 1) % viewerItems.length);
  };

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event) => {
      const target = event.target;
      if (
        target instanceof HTMLElement
        && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
      ) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeViewer();
        return;
      }
      if (!hasCarousel) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        goPrev();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        goNext();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [closeViewer, goNext, goPrev, hasCarousel, open]);

  const renderMedia = (displaySrc, displayAlt, kind, mode = "preview") => {
    const baseClass = mode === "viewer" ? "viewer-media" : "preview-media";
    const baseStyle = mode === "viewer" ? { maxWidth: "100%", maxHeight: "100%" } : {};
    if (mode === "viewer" && kind && (kind === "image" || kind === "pdf")) {
      baseStyle.transform = `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`;
      baseStyle.transformOrigin = "center center";
      baseStyle.transition = isPanning ? "none" : "transform 90ms ease-out";
    }
    if (mode === "viewer" && kind === "image") {
      // Fill the viewport box and contain inside it so low-res images can upscale cleanly.
      baseStyle.width = "100%";
      baseStyle.height = "100%";
      baseStyle.objectFit = "contain";
      baseStyle.objectPosition = "center";
    }

    if (kind === "image") {
      return (
        <img
          ref={mode === "viewer" ? activeMediaRef : null}
          src={displaySrc}
          alt={displayAlt}
          className={`${baseClass} media-img`}
          style={baseStyle}
          onLoad={() => {
            if (mode !== "viewer") return;
            const media = activeMediaRef.current;
            if (media instanceof HTMLImageElement) {
              const naturalWidth = media.naturalWidth || 0;
              const naturalHeight = media.naturalHeight || 0;
              setMediaNaturalSize({
                width: naturalWidth,
                height: naturalHeight,
              });
              if (naturalWidth > 0 && naturalHeight > 0) {
                const ratio = naturalWidth / naturalHeight;
                if (ratio >= VIEWPORT_WIDE_THRESHOLD) {
                  setViewportBoost({ width: VIEWPORT_BOOST_FACTOR, height: 1 });
                } else if (ratio <= VIEWPORT_TALL_THRESHOLD) {
                  setViewportBoost({ width: 1, height: VIEWPORT_BOOST_FACTOR });
                } else {
                  setViewportBoost({ width: 1, height: 1 });
                }
              } else {
                setViewportBoost({ width: 1, height: 1 });
              }
            }
            window.requestAnimationFrame(() => {
              setPan((current) => clampPan(current, zoom));
            });
          }}
          loading="lazy"
        />
      );
    }
    if (kind === "pdf") {
      return (
        <embed
          ref={mode === "viewer" ? activeMediaRef : null}
          src={displaySrc}
          type="application/pdf"
          title={displayAlt || "pdf document"}
          className={`${baseClass} pdf-embed`}
          style={baseStyle}
        />
      );
    }
    if (kind === "video") {
      return (
        <video
          src={displaySrc}
          controls={mode === "viewer"}
          className={`${baseClass} video-player`}
          style={baseStyle}
        />
      );
    }
    if (kind === "audio") {
      return (
        <audio
          src={displaySrc}
          controls
          className={`${baseClass} audio-player`}
          style={baseStyle}
        />
      );
    }
    if (mode === "viewer") {
      return (
        <a
          href={displaySrc}
          target="_blank"
          rel="noopener noreferrer"
          className="viewer-link-out"
        >
          {displayAlt || displaySrc}
        </a>
      );
    }
    return <span className="generic-placeholder">{displayAlt || "file"}</span>;
  };

  const previewExtension = cleanExtension(src);
  const previewKind = classifyMedia(previewExtension);
  const showCaptionEditButton = activeKind === "image" && displayCaption && !captionEditOpen;
  const zoomPercent = Math.round(zoom * 100);
  const zoomMinPercent = Math.round(ZOOM_MIN * 100);
  const zoomMaxPercent = Math.round(ZOOM_MAX * 100);
  const surfaceMaxWidth = Math.round(BASE_SURFACE_MAX_WIDTH_PX * viewportBoost.width);
  const wrapperHeightVh = Math.min(
    MAX_WRAPPER_HEIGHT_VH,
    BASE_WRAPPER_HEIGHT_VH * viewportBoost.height,
  );
  const dynamicWrapperHeight = `min(${wrapperHeightVh.toFixed(1)}vh, calc(96vh - 220px))`;
  const viewerSurfaceStyle = {
    width: `min(96vw, ${surfaceMaxWidth}px)`,
    maxHeight: "96vh",
  };
  const viewerMediaWrapperStyle = {
    height: dynamicWrapperHeight,
    maxHeight: dynamicWrapperHeight,
  };
  const handleZoomSliderChange = (event) => {
    const raw = Number(event?.target?.value);
    if (!Number.isFinite(raw)) return;
    setZoomValue(raw / 100);
  };

  return (
    <div className="media-viewer">
      <div
        className="media-preview"
        onClick={openViewer}
        onKeyDown={handlePreviewKey}
        role="button"
        tabIndex={0}
        aria-label="Open media viewer"
      >
        {renderMedia(src, alt, previewKind, "preview")}
      </div>
      {showLink && (
        <button type="button" className="viewer-inline-btn" onClick={openViewer}>
          open in viewer
        </button>
      )}
      {open && (
        <dialog
          ref={dialogRef}
          className="viewer-dialog"
          open
          onClick={closeViewer}
          onWheelCapture={(event) => {
            if (isZoomable && event.ctrlKey) {
              event.preventDefault();
            }
          }}
          onCancel={(event) => {
            event.preventDefault();
            closeViewer();
          }}
        >
          <div
            className="viewer-surface"
            style={viewerSurfaceStyle}
            role="document"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="viewer-header">
              {hasCarousel ? (
                <div className="viewer-counter">
                  {activeIndex + 1} / {viewerItems.length}
                </div>
              ) : (
                <span className="viewer-counter" />
              )}
              <button
                type="button"
                className="viewer-close"
                aria-label="Close viewer"
                onClick={closeViewer}
              >
                {"x"}
              </button>
            </div>
            <div className="viewer-toolbar">
              {isZoomable ? (
                <div className="toolbar-group">
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={() => adjustZoom(-ZOOM_STEP)}
                    disabled={zoom <= ZOOM_MIN}
                    title="Zoom out (Ctrl + wheel)"
                  >
                    -
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={() => adjustZoom(ZOOM_STEP)}
                    disabled={zoom >= ZOOM_MAX}
                    title="Zoom in (Ctrl + wheel)"
                  >
                    +
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={resetZoom}
                    disabled={Math.abs(zoom - 1) < 0.01}
                    title="Reset zoom"
                  >
                    reset
                  </button>
                  <span className="zoom-indicator">{zoomPercent}%</span>
                  <input
                    className="zoom-slider"
                    type="range"
                    min={zoomMinPercent}
                    max={zoomMaxPercent}
                    step={Math.round(ZOOM_STEP * 100)}
                    value={zoomPercent}
                    onChange={handleZoomSliderChange}
                    aria-label="Zoom level"
                  />
                </div>
              ) : (
                <span className="toolbar-spacer" />
              )}
              <div className="toolbar-group">
                {activeKind === "image" && (
                  <button
                    type="button"
                    className="viewer-btn viewer-btn--mint"
                    onClick={() => {
                      setCaptionError("");
                      if (showCaptionEditButton) {
                        setCaptionEditOpen(true);
                        setCaptionDraft(storedCaption);
                        return;
                      }
                      setCaptionEditOpen((prev) => !prev);
                    }}
                    disabled={captionLoading}
                    title={
                      showCaptionEditButton
                        ? "Edit stored caption text."
                        : "Generate and index a retrieval caption, then store a readable caption."
                    }
                  >
                    {showCaptionEditButton ? "edit caption" : "caption image"}
                  </button>
                )}
                <button
                  type="button"
                  className="viewer-btn"
                  onClick={() => window.open(activeSrc, "_blank", "noopener,noreferrer")}
                  title="Open this file directly in a new tab"
                >
                  open file
                </button>
                {activeHash && (
                  <button
                    type="button"
                    className="viewer-btn viewer-btn--mint"
                    onClick={handleReveal}
                    disabled={revealBusy}
                    title="Reveal this attachment folder in your OS file browser. If blocked, the path will be shown below."
                  >
                    {revealBusy ? "opening..." : "open folder"}
                  </button>
                )}
              </div>
            </div>
            <div className="viewer-file-title" title={activeName}>
              {activeName}
            </div>
            <div className="viewer-meta">
              <span className="viewer-meta-items">
                {activeExtension ? `.${activeExtension}` : "file"}
                {activeSize ? ` | ${formatBytes(activeSize)}` : ""}
                {activeUploadedAt ? ` | ${formatUploadedAt(activeUploadedAt)}` : ""}
                {activeHash ? ` | ${activeHash.slice(0, 12)}...` : ""}
              </span>
            </div>
            {activeStatusDetails.length > 0 && (
              <div className="viewer-status-note">
                {activeStatusDetails.join(" | ")}
              </div>
            )}
            <div className="viewer-main">
              {hasCarousel && (
                <button
                  type="button"
                  className="viewer-nav viewer-nav--prev"
                  aria-label="Previous media"
                  title="Previous item"
                  onClick={(event) => {
                    event.stopPropagation();
                    goPrev();
                  }}
                >
                  {"<"}
                </button>
              )}
              <div
                ref={mediaWrapperRef}
                className={`viewer-media-wrapper ${
                  isZoomable ? "viewer-media-wrapper--zoomable" : ""
                }${isZoomed ? " viewer-media-wrapper--zoomed" : ""}${
                  isPanning ? " is-panning" : ""
                }`}
                style={viewerMediaWrapperStyle}
                onWheelCapture={handleViewerWheel}
                onWheel={handleViewerWheel}
                onPointerDown={beginPan}
                onPointerMove={movePan}
                onPointerUp={endPan}
                onPointerCancel={endPan}
                onPointerLeave={(event) => {
                  if (isPanning) endPan(event);
                }}
              >
                {renderMedia(activeSrc, activeAlt, activeKind, "viewer")}
              </div>
              {hasCarousel && (
                <button
                  type="button"
                  className="viewer-nav viewer-nav--next"
                  aria-label="Next media"
                  title="Next item"
                  onClick={(event) => {
                    event.stopPropagation();
                    goNext();
                  }}
                >
                  {">"}
                </button>
              )}
            </div>
            {captionEditOpen && activeKind === "image" && (
              <div className="viewer-caption-editor">
                <textarea
                  value={captionDraft}
                  onChange={(event) => setCaptionDraft(event.target.value)}
                  placeholder="Write or generate a caption..."
                  rows={3}
                />
                <div className="viewer-caption-actions">
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={generateCaption}
                    disabled={captionGenerating}
                    title="Generate and index retrieval embeddings, then save a readable caption."
                  >
                    {captionGenerating ? "generating..." : "generate"}
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={saveCaption}
                    disabled={captionSaving || !captionDraft.trim()}
                    title="Save the current human-readable caption"
                  >
                    {captionSaving ? "saving..." : "save"}
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={deleteCaption}
                    disabled={captionDeleting || (!captionDraft.trim() && !storedCaption)}
                    title="Delete the stored caption for this attachment"
                  >
                    {captionDeleting ? "deleting..." : "delete"}
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={loadCaptionModelInfo}
                    disabled={captionModelInfoBusy}
                    title="Show model details used for captioning and retrieval. Change these in Settings > Models."
                  >
                    {captionModelInfoBusy ? "loading models..." : "models"}
                  </button>
                  <button
                    type="button"
                    className="viewer-btn"
                    onClick={() => {
                      setCaptionEditOpen(false);
                      setCaptionDraft(storedCaption || "");
                    }}
                    title="Close caption editor"
                  >
                    close
                  </button>
                </div>
                {!activeHash && (
                  <div className="viewer-status-note">
                    Save/delete require an uploaded attachment item.
                  </div>
                )}
                {captionModelInfoError && (
                  <div className="viewer-status-note">{captionModelInfoError}</div>
                )}
                {captionModelInfo && (
                  <div className="viewer-status-note">
                    caption model: {captionModelInfo.visionModel} | text embeddings:{" "}
                    {captionModelInfo.ragEmbeddingModel} | image embeddings:{" "}
                    {captionModelInfo.ragClipModel}
                  </div>
                )}
              </div>
            )}
            {!captionEditOpen && displayCaption && (
              <div className="viewer-caption-wrap">
                <button
                  type="button"
                  className="viewer-caption viewer-caption-btn"
                  onClick={() => {
                    setCaptionError("");
                    setCaptionDraft(displayCaption);
                    setCaptionEditOpen(true);
                  }}
                  title="Caption saved. Click to edit or regenerate."
                >
                  {displayCaption}
                </button>
                <div className="viewer-caption-aside">
                  {activeCaptionStatus && (
                    <span className="viewer-caption-badge" title={`caption: ${activeCaptionStatus}`}>
                      {activeCaptionStatus}
                    </span>
                  )}
                  {activePlaceholderCaption && (
                    <span className="viewer-caption-badge viewer-caption-badge--placeholder">
                      placeholder
                    </span>
                  )}
                  {activeHash && (
                    <button
                      type="button"
                      className="viewer-caption-saved viewer-caption-saved-btn"
                      title="Saved for retrieval. Click to open Memory focused to this image key."
                      onClick={handleViewSavedMemory}
                    >
                      {"\u2713"} saved
                    </button>
                  )}
                </div>
              </div>
            )}
            {error && <div className="viewer-error">{error}</div>}
            {captionError && <div className="viewer-error">{captionError}</div>}
            {revealInfo?.path && (
              <div className="viewer-status">
                stored at <code>{revealInfo.path}</code>
                {typeof revealInfo.opened === "boolean" && (
                  <span className="viewer-status-note">
                    {revealInfo.opened
                      ? " (opened in system file browser)"
                      : " (could not open automatically)"}
                  </span>
                )}
                {!revealInfo.opened && revealInfo.open_uri && (
                  <span className="viewer-status-note">
                    {" "}
                    fallback: <code>{revealInfo.open_uri}</code>
                  </span>
                )}
              </div>
            )}
            {revealError && <div className="viewer-error">{revealError}</div>}
          </div>
        </dialog>
      )}
    </div>
  );
};

export default MediaViewer;
