import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import SwapVertIcon from "@mui/icons-material/SwapVert";
import { useNavigate, useSearchParams } from "react-router-dom";
import "../styles/ThreadsTab.css";
import FilterBar from "./FilterBar";
import { GlobalContext } from "../main";

// Parse "#rrggbb" into RGB channels for contrast calculations.
const parseHexColor = (value) => {
  const raw = String(value || "").trim().replace("#", "");
  if (!/^[\da-fA-F]{6}$/.test(raw)) return null;
  return {
    r: Number.parseInt(raw.slice(0, 2), 16),
    g: Number.parseInt(raw.slice(2, 4), 16),
    b: Number.parseInt(raw.slice(4, 6), 16),
  };
};

const toLinearLuminance = (channel) => {
  const value = Number(channel) / 255;
  if (value <= 0.03928) return value / 12.92;
  return ((value + 0.055) / 1.055) ** 2.4;
};

const getRelativeLuminance = (rgb) => {
  if (!rgb) return 0;
  const red = toLinearLuminance(rgb.r);
  const green = toLinearLuminance(rgb.g);
  const blue = toLinearLuminance(rgb.b);
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
};

const getContrastRatio = (leftHex, rightHex) => {
  const left = getRelativeLuminance(parseHexColor(leftHex));
  const right = getRelativeLuminance(parseHexColor(rightHex));
  const brightest = Math.max(left, right);
  const darkest = Math.min(left, right);
  return (brightest + 0.05) / (darkest + 0.05);
};

// Pick dark/light foreground text to keep thread labels readable on the card color.
const pickReadableTextColor = (backgroundHex) => {
  const dark = "#111322";
  const light = "#f7f9ff";
  const darkContrast = getContrastRatio(backgroundHex, dark);
  const lightContrast = getContrastRatio(backgroundHex, light);
  return darkContrast >= lightContrast ? dark : light;
};

// Fixed, warm/cool pastel palette for thread chips and chips-to-snippet color linking.
const THREAD_TONES = [
  { bg: "#ffd9ec", border: "#de79ab" },
  { bg: "#ffd6ad", border: "#ffa759" },
  { bg: "#ffe7a4", border: "#d9a223" },
  { bg: "#c7f3d7", border: "#49ad6d" },
  { bg: "#c8eef5", border: "#4c9bbb" },
  { bg: "#d6dcff", border: "#6274ca" },
  { bg: "#e6d7ff", border: "#8b67c8" },
  { bg: "#ffd4e2", border: "#cf6f95" },
].map((tone) => ({
  ...tone,
  text: pickReadableTextColor(tone.bg),
}));

const MAX_THREAD_DEPTH = 6;
const MIN_SUBTHREAD_MESSAGE_COUNT = 1;
const SUBTHREAD_PATH_DELIMITER = "||";

// Normalize conversation identifiers for stable card keys and URL params.
const normalizeConversationName = (value) => {
  const raw = String(value || "").trim().replaceAll("\\", "/");
  if (!raw) return "";
  const withoutAnchor = raw.split("#", 1)[0].trim();
  if (withoutAnchor.toLowerCase().endsWith(".json")) {
    return withoutAnchor.slice(0, -5);
  }
  return withoutAnchor;
};

// Convert date-ish values into timestamps so sorting and recency math never crashes.
const parseDateToTimestamp = (value) => {
  if (!value) return null;
  const ts = Date.parse(String(value));
  return Number.isFinite(ts) ? ts : null;
};

const formatTimestampLabel = (value) => {
  const ts = parseDateToTimestamp(value);
  if (!Number.isFinite(ts)) return "unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(ts));
};

const normalizeMessageRole = (message) => {
  const raw = String(message?.role || message?.speaker || "message")
    .trim()
    .toLowerCase();
  if (!raw) return "message";
  if (raw === "ai") return "assistant";
  return raw;
};

const formatMessageRole = (messageRole) => {
  const normalized = normalizeMessageRole(messageRole);
  if (normalized === "assistant") return "A";
  if (normalized === "user") return "U";
  return "M";
};

const extractMessageText = (message) => {
  if (!message || typeof message !== "object") return "";
  if (typeof message.text === "string" && message.text.trim()) return message.text;
  if (typeof message.content === "string" && message.content.trim()) return message.content;
  if (Array.isArray(message.content)) {
    const parts = message.content
      .map((part) => {
        if (!part) return "";
        if (typeof part === "string") return part;
        if (typeof part.text === "string") return part.text;
        if (typeof part.content === "string") return part.content;
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("\n");
  }
  return "";
};

const sortMentions = (left, right) =>
  String(right?.date || "").localeCompare(String(left?.date || ""))
  || Number(right?.score || 0) - Number(left?.score || 0)
  || String(left?.conversation || "").localeCompare(String(right?.conversation || ""));

const buildThreadOverviewFallback = (threadsMap) => {
  const entries = Object.entries(threadsMap || {});
  return entries
    .map(([label, rawItems], index) => {
      const items = Array.isArray(rawItems) ? rawItems : [];
      const byConversation = new Map();
      const messageRefs = new Set();

      items.forEach((item) => {
        const conversation = normalizeConversationName(item?.conversation) || "(unknown)";
        const score = Number(item?.score);
        const parsedScore = Number.isFinite(score) ? score : null;
        const messageIndex = Number.isInteger(item?.message_index)
          ? item.message_index
          : Number.isFinite(Number(item?.message_index))
            ? Number(item.message_index)
            : null;
        const date = String(item?.date || "");
        const excerpt = String(item?.excerpt || "");
        const entry = byConversation.get(conversation) || {
          conversation,
          item_count: 0,
          message_set: new Set(),
          latest_date: "",
          score_total: 0,
          score_count: 0,
          preview_excerpt: "",
        };
        entry.item_count += 1;
        if (messageIndex !== null) {
          entry.message_set.add(messageIndex);
          messageRefs.add(`${conversation}:${messageIndex}`);
        }
        if (date && (!entry.latest_date || date > entry.latest_date)) {
          entry.latest_date = date;
        }
        if (parsedScore !== null) {
          entry.score_total += parsedScore;
          entry.score_count += 1;
        }
        if (excerpt && !entry.preview_excerpt) {
          entry.preview_excerpt = excerpt;
        }
        byConversation.set(conversation, entry);
      });

      const conversation_breakdown = Array.from(byConversation.values())
        .map((item) => ({
          conversation: item.conversation,
          item_count: item.item_count,
          message_count: item.message_set.size,
          latest_date: item.latest_date,
          avg_score:
            item.score_count > 0
              ? Number((item.score_total / item.score_count).toFixed(4))
              : null,
          preview_excerpt: item.preview_excerpt,
        }))
        .sort(
          (left, right) =>
            Number(right.item_count || 0) - Number(left.item_count || 0)
            || String(left.conversation || "").localeCompare(String(right.conversation || "")),
        );

      const top_examples = items
        .map((item) => ({
          conversation: normalizeConversationName(item?.conversation) || "(unknown)",
          message_index:
            Number.isInteger(item?.message_index)
            || Number.isFinite(Number(item?.message_index))
              ? Number(item.message_index)
              : null,
          date: String(item?.date || ""),
          score: Number.isFinite(Number(item?.score)) ? Number(item.score) : null,
          excerpt: String(item?.excerpt || ""),
        }))
        .sort(sortMentions)
        .slice(0, 3);

      return {
        id: `fallback-${index}-${String(label || "").toLowerCase().replaceAll(" ", "-")}`,
        label: String(label || ""),
        item_count: items.length,
        conversation_count: conversation_breakdown.length,
        message_count: messageRefs.size || items.length,
        palette_index: index,
        top_examples,
        conversation_breakdown,
      };
    })
    .sort(
      (left, right) =>
        Number(right.item_count || 0) - Number(left.item_count || 0)
        || String(left.label || "").localeCompare(String(right.label || "")),
    );
};

// Group thread mentions by conversation so the selected thread panel can render one row per conversation.
const groupThreadMentionsByConversation = (items) => {
  const grouped = new Map();
  (Array.isArray(items) ? items : []).forEach((raw) => {
    const conversation = normalizeConversationName(raw?.conversation) || "(unknown)";
    const messageIndex =
      Number.isInteger(raw?.message_index) || Number.isFinite(Number(raw?.message_index))
        ? Number(raw.message_index)
        : null;
    const score = Number(raw?.score);
    const parsedScore = Number.isFinite(score) ? score : null;
    const date = String(raw?.date || "");
    const excerpt = String(raw?.excerpt || "");
    const row = grouped.get(conversation) || {
      conversation,
      itemCount: 0,
      messageSet: new Set(),
      latestDate: "",
      scoreTotal: 0,
      scoreCount: 0,
      previewExcerpt: "",
      references: [],
    };
    row.itemCount += 1;
    if (messageIndex !== null) {
      row.messageSet.add(messageIndex);
    }
    if (date && (!row.latestDate || date > row.latestDate)) {
      row.latestDate = date;
    }
    if (parsedScore !== null) {
      row.scoreTotal += parsedScore;
      row.scoreCount += 1;
    }
    if (excerpt && !row.previewExcerpt) {
      row.previewExcerpt = excerpt;
    }
    row.references.push({
      conversation,
      message_index: messageIndex,
      date,
      score: parsedScore,
      excerpt,
    });
    grouped.set(conversation, row);
  });

  return Array.from(grouped.values())
    .map((row) => ({
      conversation: row.conversation,
      itemCount: row.itemCount,
      messageCount: row.messageSet.size,
      latestDate: row.latestDate,
      avgScore: row.scoreCount > 0 ? Number((row.scoreTotal / row.scoreCount).toFixed(4)) : null,
      previewExcerpt: row.previewExcerpt,
      references: row.references.sort(sortMentions),
    }))
    .sort(
      (left, right) =>
        Number(right.itemCount || 0) - Number(left.itemCount || 0)
        || String(left.conversation || "").localeCompare(String(right.conversation || "")),
    );
};

const SNIPPET_SORT_OPTIONS = [
  { id: "date", label: "Date" },
  { id: "similarity", label: "Similarity" },
  { id: "custom", label: "Custom" },
];
const SNIPPET_SORT_MODE_ORDER = SNIPPET_SORT_OPTIONS.map((option) => option.id);
const NOOP_SET_STATE = () => {};

const THREAD_SIGNAL_MODE_OPTIONS = [
  { id: "embeddings", label: "Embeddings only (current stable)" },
  { id: "hybrid", label: "Hybrid: sparse SAE proxy + embeddings" },
  { id: "sae", label: "Sparse SAE proxy only" },
];

const THREAD_CLUSTER_BACKEND_OPTIONS = [
  { id: "sklearn", label: "scikit-learn (stable)" },
  { id: "torch", label: "PyTorch k-means (experimental)" },
];

const THREAD_CLUSTER_DEVICE_OPTIONS = [
  { id: "auto", label: "auto" },
  { id: "cpu", label: "cpu" },
  { id: "cuda", label: "cuda" },
];

const SAE_COMBO_PRESETS = [
  "openai/gpt-oss-20b :: future SAE pack",
  "google/gemma-2-2b :: Gemma Scope",
  "custom",
];

const normalizeThreadSignalMode = (value) => {
  const mode = String(value || "").trim().toLowerCase();
  if (mode === "sae") return "sae";
  if (mode === "hybrid") return "hybrid";
  return "embeddings";
};

const normalizeThreadSignalBlend = (value, fallback = 0.7) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(1, Math.max(0, parsed));
};

const THREAD_BUNDLE_STORAGE_KEY = "float:threads:topic-bundles";

const parseCommaSeparatedTopics = (value) =>
  String(value || "")
    .split(",")
    .map((topic) => topic.trim())
    .filter(Boolean);

const mergeTopicLists = (baseTopics, nextTopics) => {
  const seen = new Set();
  const merged = [];
  [...baseTopics, ...nextTopics].forEach((topic) => {
    const normalized = String(topic || "").trim();
    if (!normalized) return;
    const key = normalized.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(normalized);
  });
  return merged;
};

const sanitizeTopicBundles = (raw) => {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry, index) => {
      const name = String(entry?.name || "").trim();
      const topics = Array.isArray(entry?.topics)
        ? entry.topics.map((topic) => String(topic || "").trim()).filter(Boolean)
        : [];
      if (!name || !topics.length) return null;
      return {
        id: String(entry?.id || `bundle-${index}`),
        name,
        topics: mergeTopicLists([], topics),
      };
    })
    .filter(Boolean);
};

const getSnippetKey = (snippet) => [
  normalizeConversationName(snippet?.conversation) || "",
  Number.isInteger(Number(snippet?.message_index)) ? Number(snippet.message_index) : "",
  String(snippet?.date || ""),
  String(snippet?.excerpt || "").trim().slice(0, 160),
].join("|");

const getThreadTone = (index) => THREAD_TONES[Math.abs(Number(index || 0)) % THREAD_TONES.length];

const ThreadsTab = () => {
  // Source data + UI state. Search params are the source of truth for selected thread/conversation.
  const globalContext = useContext(GlobalContext);
  const setState =
    typeof globalContext?.setState === "function"
      ? globalContext.setState
      : NOOP_SET_STATE;
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [inferTopics, setInferTopics] = useState(true);
  const [customTags, setCustomTags] = useState("");
  const [manualThreads, setManualThreads] = useState("");
  const [kOption, setKOption] = useState("auto");
  const [preferredK, setPreferredK] = useState("16");
  const [maxK, setMaxK] = useState("30");
  const [coalesceRelated, setCoalesceRelated] = useState(true);
  const [scopeMode, setScopeMode] = useState("all");
  const [scopeFolder, setScopeFolder] = useState("");
  const [scopeThreadInput, setScopeThreadInput] = useState("");
  const [topN, setTopN] = useState("5");
  const [saeEnabled, setSaeEnabled] = useState(false);
  const [saeMode, setSaeMode] = useState("inspect");
  const [saeLayer, setSaeLayer] = useState("12");
  const [saeTopK, setSaeTopK] = useState("20");
  const [saeTokenPositions, setSaeTokenPositions] = useState("all");
  const [saeFeatures, setSaeFeatures] = useState("123:+0.8,91:-0.4");
  const [saeDryRun, setSaeDryRun] = useState(true);
  const [threadSignalMode, setThreadSignalMode] = useState("hybrid");
  const [threadSignalBlend, setThreadSignalBlend] = useState(0.7);
  const [clusterBackend, setClusterBackend] = useState("sklearn");
  const [clusterDevice, setClusterDevice] = useState("auto");
  const [saeModelCombo, setSaeModelCombo] = useState(SAE_COMBO_PRESETS[0]);
  const [saeEmbeddingsFallback, setSaeEmbeddingsFallback] = useState(true);
  const [saeLiveInspectConsole, setSaeLiveInspectConsole] = useState(false);
  const [snippetSortMode, setSnippetSortMode] = useState("date");
  const [selectedSnippetKey, setSelectedSnippetKey] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [subthreadSearchByDepth, setSubthreadSearchByDepth] = useState({});
  const [subthreadsOpen, setSubthreadsOpen] = useState(false);
  const [searchRes, setSearchRes] = useState([]);
  const [searchAttempted, setSearchAttempted] = useState(false);
  const [renameTarget, setRenameTarget] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState("");
  const [topicBundles, setTopicBundles] = useState([]);
  const [selectedBundleId, setSelectedBundleId] = useState("auto:suggested");
  const [bundleNameInput, setBundleNameInput] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [topBarCollapsed, setTopBarCollapsed] = useState(false);
  const [conversationCache, setConversationCache] = useState({});
  const [conversationLoadingKey, setConversationLoadingKey] = useState("");
  const [focusedConversation, setFocusedConversation] = useState("");
  const inlineMessageRefs = useRef(new Map());
  const galleryStripRef = useRef(null);
  const galleryCardRefs = useRef(new Map());
  const galleryWheelFrameRef = useRef(0);
  const galleryWheelTargetRef = useRef(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeThread = (searchParams.get("thread") || "").trim();
  const activeSubthread = (searchParams.get("subthread") || "").trim();
  const activeConversationParam = normalizeConversationName(searchParams.get("conv") || "");
  const parsedActiveMessage = Number(searchParams.get("msg"));
  const activeMessageParam =
    Number.isInteger(parsedActiveMessage) && parsedActiveMessage >= 0
      ? parsedActiveMessage
      : null;

  // Guard helper for form inputs that should stay numeric and positive.
  const parseIntInput = useCallback((value, fallback) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 1 ? parsed : fallback;
  }, []);

  // Sync generation form fields with the latest backend metadata when summary refreshes.
  const loadOptionsFromSummary = useCallback((nextSummary) => {
    const hints = nextSummary?.metadata?.ui_hints;
    if (!hints || typeof hints !== "object") {
      return;
    }
    setInferTopics(
      typeof hints.infer_topics === "boolean" ? hints.infer_topics : true,
    );
    if (hints.k_option === "auto" || hints.k_option === null) {
      setKOption("auto");
    } else if (typeof hints.k_option === "number" && Number.isFinite(hints.k_option)) {
      setKOption(String(hints.k_option));
    }
    setPreferredK(String(parseIntInput(hints.preferred_k, 16)));
    setMaxK(String(parseIntInput(hints.max_k, 30)));
    if (typeof hints.coalesce_related === "boolean") {
      setCoalesceRelated(hints.coalesce_related);
    }
    if (
      hints.scope_mode === "folder"
      || hints.scope_mode === "thread"
      || hints.scope_mode === "all"
    ) {
      setScopeMode(hints.scope_mode);
    }
    setScopeFolder(typeof hints.scope_folder === "string" ? hints.scope_folder : "");
    setScopeThreadInput(
      typeof hints.scope_thread === "string" ? hints.scope_thread : "",
    );
    setTopN(String(parseIntInput(hints.top_n, 5)));
    setClusterBackend(
      typeof hints.cluster_backend_requested === "string" && hints.cluster_backend_requested.trim()
        ? hints.cluster_backend_requested.trim()
        : typeof hints.cluster_backend === "string" && hints.cluster_backend.trim()
          ? hints.cluster_backend.trim()
          : "sklearn",
    );
    setClusterDevice(
      typeof hints.cluster_device_requested === "string" && hints.cluster_device_requested.trim()
        ? hints.cluster_device_requested.trim()
        : typeof hints.cluster_device === "string" && hints.cluster_device.trim()
          ? hints.cluster_device.trim()
          : "auto",
    );
    setThreadSignalMode(
      normalizeThreadSignalMode(
        hints.thread_signal_mode ?? hints.experimental_thread_signal_mode,
      ),
    );
    setThreadSignalBlend(
      normalizeThreadSignalBlend(
        hints.thread_signal_blend,
        0.7,
      ),
    );
    setSaeModelCombo(
      typeof hints.sae_model_combo === "string" && hints.sae_model_combo.trim()
        ? hints.sae_model_combo.trim()
        : SAE_COMBO_PRESETS[0],
    );
    setSaeEmbeddingsFallback(
      typeof hints.sae_embeddings_fallback === "boolean"
        ? hints.sae_embeddings_fallback
        : true,
    );
    setSaeLiveInspectConsole(
      typeof hints.sae_live_inspect_console === "boolean"
        ? hints.sae_live_inspect_console
        : false,
    );

    const sae = hints.experimental_sae;
    if (sae && typeof sae === "object") {
      const parsedLayer = Number(sae.layer);
      setSaeEnabled(Boolean(sae.enabled));
      setSaeMode(
        sae.mode === "steer" || sae.mode === "inspect" ? sae.mode : "inspect",
      );
      setSaeLayer(
        String(Number.isFinite(parsedLayer) && parsedLayer >= 0 ? parsedLayer : 12),
      );
      setSaeTopK(String(parseIntInput(sae.topk, 20)));
      setSaeTokenPositions(
        typeof sae.token_positions === "string" && sae.token_positions.trim()
          ? sae.token_positions.trim()
          : "all",
      );
      setSaeFeatures(typeof sae.features === "string" ? sae.features : "");
      setSaeDryRun(
        typeof sae.dry_run === "boolean" ? sae.dry_run : true,
      );
      setThreadSignalMode(
        normalizeThreadSignalMode(sae.retrieval_mode ?? hints.thread_signal_mode),
      );
      setThreadSignalBlend(
        normalizeThreadSignalBlend(
          sae.retrieval_blend ?? hints.thread_signal_blend,
          0.7,
        ),
      );
      setSaeModelCombo(
        typeof sae.model_combo === "string" && sae.model_combo.trim()
          ? sae.model_combo.trim()
          : typeof hints.sae_model_combo === "string" && hints.sae_model_combo.trim()
            ? hints.sae_model_combo.trim()
            : SAE_COMBO_PRESETS[0],
      );
      setSaeEmbeddingsFallback(
        typeof sae.embeddings_fallback === "boolean"
          ? sae.embeddings_fallback
          : typeof hints.sae_embeddings_fallback === "boolean"
            ? hints.sae_embeddings_fallback
            : true,
      );
      setSaeLiveInspectConsole(
        typeof sae.live_inspect_console === "boolean"
          ? sae.live_inspect_console
          : typeof hints.sae_live_inspect_console === "boolean"
            ? hints.sae_live_inspect_console
            : false,
      );
    }
  }, [parseIntInput]);

  const loadSummary = async () => {
    try {
      const res = await axios.get("/api/threads/summary");
      setSummary(res.data.summary || null);
    } catch {
      // Ignore passive load failures.
    }
  };

  useEffect(() => {
    loadSummary();
  }, []);

  useEffect(() => {
    if (activeThread) {
      setScopeThreadInput(activeThread);
    }
  }, [activeThread]);

  useEffect(() => {
    setSubthreadSearchByDepth({});
    setSubthreadsOpen(Boolean(activeThread));
  }, [activeThread]);

  useEffect(() => {
    if (!optionsOpen) {
      loadOptionsFromSummary(summary);
    }
  }, [optionsOpen, summary, loadOptionsFromSummary]);

  useEffect(() => {
    if (!searchQ.trim()) {
      setSearchAttempted(false);
      setSearchRes([]);
    }
  }, [searchQ]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(THREAD_BUNDLE_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const sanitized = sanitizeTopicBundles(parsed);
      if (sanitized.length) {
        setTopicBundles(sanitized);
      }
    } catch {
      // Ignore malformed local storage data.
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(
        THREAD_BUNDLE_STORAGE_KEY,
        JSON.stringify(sanitizeTopicBundles(topicBundles)),
      );
    } catch {
      // Ignore local storage write failures.
    }
  }, [topicBundles]);

  // Kick thread generation with the current modal controls and keep loading/error state simple.
  const generate = useCallback(async (overrides = {}) => {
    const resolvedScopeMode = String(overrides.scopeMode || scopeMode || "all").trim();
    const resolvedScopeThread = String(
      overrides.scopeThread ?? scopeThreadInput ?? activeThread ?? "",
    ).trim();
    if (resolvedScopeMode === "thread" && !resolvedScopeThread) {
      setError("Select or enter a thread name to refine.");
      return false;
    }
    setLoading(true);
    setError("");
    try {
      const parsedPreferredK = Number(preferredK);
      const parsedMaxK = Number(maxK);
      const parsedTopN = Number(topN);
      const parsedSaeLayer = Number(saeLayer);
      const parsedSaeTopK = Number(saeTopK);
      const parsedSignalBlend = normalizeThreadSignalBlend(threadSignalBlend, 0.7);
      const tags = customTags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean);
      const payload = {
        infer_topics: inferTopics,
        tags: tags.length ? tags : null,
        k_option: kOption === "auto" ? null : Number(kOption),
        preferred_k:
          kOption === "auto" && Number.isFinite(parsedPreferredK) && parsedPreferredK >= 2
            ? parsedPreferredK
            : null,
        max_k:
          kOption === "auto" && Number.isFinite(parsedMaxK) && parsedMaxK >= 2
            ? parsedMaxK
            : null,
        cluster_backend: clusterBackend,
        cluster_device: clusterDevice,
        coalesce_related: coalesceRelated,
        scope_folder: resolvedScopeMode === "folder" ? scopeFolder.trim() || null : null,
        scope_thread: resolvedScopeMode === "thread" ? resolvedScopeThread : null,
        manual_threads: manualThreads
          .split(",")
          .map((thread) => thread.trim())
          .filter(Boolean),
        top_n: Number.isFinite(parsedTopN) && parsedTopN > 0 ? parsedTopN : null,
        thread_signal_mode: normalizeThreadSignalMode(threadSignalMode),
        thread_signal_blend: parsedSignalBlend,
        sae_model_combo: saeModelCombo.trim() || null,
        sae_embeddings_fallback: saeEmbeddingsFallback,
        sae_live_inspect_console: saeLiveInspectConsole,
        sae_options: {
          enabled: saeEnabled,
          mode: saeMode,
          layer:
            Number.isFinite(parsedSaeLayer) && parsedSaeLayer >= 0
              ? parsedSaeLayer
              : null,
          topk:
            Number.isFinite(parsedSaeTopK) && parsedSaeTopK >= 1
              ? parsedSaeTopK
              : null,
          token_positions: saeTokenPositions.trim() || "all",
          features: saeFeatures.trim(),
          dry_run: saeDryRun,
          retrieval_mode: normalizeThreadSignalMode(threadSignalMode),
          retrieval_blend: parsedSignalBlend,
          model_combo: saeModelCombo.trim() || "",
          embeddings_fallback: saeEmbeddingsFallback,
          live_inspect_console: saeLiveInspectConsole,
        },
      };
      const res = await axios.post("/api/threads/generate", payload);
      setSummary(res.data.summary || null);
      return true;
    } catch (err) {
      setError(err?.response?.data?.detail || "Failed to generate threads. Check server logs.");
      return false;
    } finally {
      setLoading(false);
    }
  }, [
    activeThread,
    clusterBackend,
    clusterDevice,
    coalesceRelated,
    customTags,
    inferTopics,
    kOption,
    manualThreads,
    maxK,
    preferredK,
    saeDryRun,
    saeEmbeddingsFallback,
    saeEnabled,
    saeFeatures,
    saeLayer,
    saeLiveInspectConsole,
    saeMode,
    saeModelCombo,
    saeTokenPositions,
    saeTopK,
    scopeFolder,
    scopeMode,
    scopeThreadInput,
    threadSignalBlend,
    threadSignalMode,
    topN,
  ]);

  const generateFromModal = async () => {
    const ok = await generate();
    if (ok) {
      setOptionsOpen(false);
    }
  };
  const doSearch = async () => {
    const query = searchQ.trim();
    if (!query) {
      setSearchAttempted(false);
      setSearchRes([]);
      return;
    }
    setSearching(true);
    setSearchAttempted(true);
    try {
      const res = await axios.post("/api/threads/search", { query, top_k: 20 });
      setSearchRes(Array.isArray(res.data?.matches) ? res.data.matches : []);
    } catch {
      setSearchRes([]);
    } finally {
      setSearching(false);
    }
  };

  const ensureConversationLoaded = useCallback(
    async (conversationName) => {
      const normalizedConversation = normalizeConversationName(conversationName);
      if (!normalizedConversation || normalizedConversation === "(unknown)") return;
      if (conversationCache[normalizedConversation]) return;
      setConversationLoadingKey(normalizedConversation);
      try {
        const encodedName = encodeURIComponent(normalizedConversation);
        const res = await axios.get(`/api/conversations/${encodedName}`);
        const loadedMessages = Array.isArray(res?.data?.messages) ? res.data.messages : [];
        setConversationCache((prev) => (
          prev[normalizedConversation] ? prev : { ...prev, [normalizedConversation]: loadedMessages }
        ));
      } catch {
        setConversationCache((prev) => (
          prev[normalizedConversation] ? prev : { ...prev, [normalizedConversation]: [] }
        ));
      } finally {
        setConversationLoadingKey((prev) => (
          prev === normalizedConversation ? "" : prev
        ));
      }
    },
    [conversationCache],
  );

  // Keep thread/conversation/message selection in the URL so tab state survives refresh.
  const updateThreadParam = (value, options = {}) => {
    const next = new URLSearchParams(searchParams);
    const normalizedThread = String(value || "").trim();
    if (normalizedThread) {
      next.set("thread", normalizedThread);
      next.set("tab", "threads");
      if (Object.prototype.hasOwnProperty.call(options, "subthread")) {
        const nextSubthread = String(options.subthread || "").trim();
        if (
          nextSubthread
          && nextSubthread.toLowerCase() !== normalizedThread.toLowerCase()
        ) {
          next.set("subthread", nextSubthread);
          next.delete("subpath");
        } else {
          next.delete("subthread");
          next.delete("subpath");
        }
      } else if (options.clearSubthread !== false) {
        next.delete("subthread");
        next.delete("subpath");
      }
    } else {
      next.delete("thread");
      next.delete("subthread");
      next.delete("subpath");
      next.delete("conv");
      next.delete("msg");
    }

    if (options.clearConversation) {
      next.delete("conv");
      next.delete("msg");
    }

    const normalizedConversation = normalizeConversationName(options.conversation || "");
    if (normalizedConversation && normalizedConversation !== "(unknown)") {
      next.set("conv", normalizedConversation);
      if (Number.isInteger(options.messageIndex) && options.messageIndex >= 0) {
        next.set("msg", String(options.messageIndex));
      } else {
        next.delete("msg");
      }
    }

    setSearchParams(next);
    if (normalizedConversation) {
      ensureConversationLoaded(normalizedConversation);
    }
  };

  const updateSubthreadPath = useCallback((values, options = {}) => {
    const parentThread = String(activeThread || "").trim();
    if (!parentThread) return;
    const next = new URLSearchParams(searchParams);
    next.set("thread", parentThread);
    next.set("tab", "threads");
    const cleanedPath = (Array.isArray(values) ? values : [values])
      .map((entry) => String(entry || "").trim())
      .filter(Boolean)
      .filter((entry, index, list) => (
        entry.toLowerCase() !== parentThread.toLowerCase()
        && list.findIndex((candidate) => candidate.toLowerCase() === entry.toLowerCase()) === index
      ))
      .slice(0, Math.max(0, MAX_THREAD_DEPTH - 1));
    if (cleanedPath.length) {
      next.set("subthread", cleanedPath[0]);
      if (cleanedPath.length > 1) {
        next.set("subpath", cleanedPath.join(SUBTHREAD_PATH_DELIMITER));
      } else {
        next.delete("subpath");
      }
    } else {
      next.delete("subthread");
      next.delete("subpath");
    }

    if (options.clearConversation) {
      next.delete("conv");
      next.delete("msg");
    }

    const normalizedConversation = normalizeConversationName(options.conversation || "");
    if (normalizedConversation && normalizedConversation !== "(unknown)") {
      next.set("conv", normalizedConversation);
      if (Number.isInteger(options.messageIndex) && options.messageIndex >= 0) {
        next.set("msg", String(options.messageIndex));
      } else {
        next.delete("msg");
      }
    }

    setSearchParams(next);
    if (normalizedConversation) {
      ensureConversationLoaded(normalizedConversation);
    }
  }, [activeThread, ensureConversationLoaded, searchParams, setSearchParams]);

  // Keep parent thread fixed and toggle only the nested subthread focus.
  const updateSubthreadParam = useCallback((value, options = {}) => {
    const normalized = String(value || "").trim();
    updateSubthreadPath(normalized ? [normalized] : [], options);
  }, [updateSubthreadPath]);

  // Convenience to focus one conversation + optional message and preserve active thread.
  const selectConversationFocus = (conversationName, messageIndex = null) => {
    const normalizedConversation = normalizeConversationName(conversationName);
    if (!normalizedConversation || normalizedConversation === "(unknown)") return;
    const next = new URLSearchParams(searchParams);
    next.set("conv", normalizedConversation);
    if (Number.isInteger(messageIndex) && messageIndex >= 0) {
      next.set("msg", String(messageIndex));
    } else {
      next.delete("msg");
    }
    if (activeThread) {
      next.set("thread", activeThread);
      next.set("tab", "threads");
      if (activeSubthread) {
        next.set("subthread", activeSubthread);
      } else {
        next.delete("subthread");
      }
    }
    setSearchParams(next);
    ensureConversationLoaded(normalizedConversation);
  };

  const loadConversation = async (conversationName) => {
    const normalizedConversation = normalizeConversationName(conversationName);
    if (!normalizedConversation || normalizedConversation === "(unknown)") return;
    try {
      const encodedName = encodeURIComponent(normalizedConversation);
      const res = await axios.get(`/api/conversations/${encodedName}`);
      const loadedMessages = Array.isArray(res?.data?.messages) ? res.data.messages : [];
      if (typeof sessionStorage !== "undefined") {
        try {
          sessionStorage.setItem(
            `float:conv-loaded:${normalizedConversation}`,
            JSON.stringify(loadedMessages),
          );
        } catch {}
      }
      setState((prev) => ({
        ...prev,
        conversation: loadedMessages,
        sessionId: normalizedConversation,
        sessionName: normalizedConversation,
      }));
      navigate("/");
    } catch (err) {
      setError(err?.response?.data?.detail || "Failed to open conversation.");
    }
  };

  const startRename = (threadName) => {
    setRenameTarget(threadName);
    setRenameValue(threadName);
    setRenameError("");
  };

  const closeRename = () => {
    setRenameTarget("");
    setRenameValue("");
    setRenameError("");
  };

  const submitRename = async () => {
    const oldName = renameTarget.trim();
    const newName = renameValue.trim();
    if (!oldName || !newName || oldName === newName) {
      closeRename();
      return;
    }
    try {
      setRenameError("");
      const res = await axios.post("/api/threads/rename", {
        old_name: oldName,
        new_name: newName,
      });
      if (res.data?.summary) {
        setSummary(res.data.summary);
      }
      if (activeThread === oldName) {
        updateThreadParam(newName);
      }
      closeRename();
    } catch (err) {
      setRenameError(err?.response?.data?.detail || "Failed to rename thread.");
    }
  };

  const clearSearch = () => {
    setSearchQ("");
    setSearchRes([]);
    setSearchAttempted(false);
  };

  // Derive the display model from backend summary, with fallback reconstruction when needed.
  const threadsMap = summary?.threads || {};
  const threadEntries = useMemo(
    () =>
      Object.entries(threadsMap).sort(
        (left, right) =>
          (Array.isArray(right[1]) ? right[1].length : 0)
          - (Array.isArray(left[1]) ? left[1].length : 0)
          || String(left[0]).localeCompare(String(right[0])),
      ),
    [threadsMap],
  );
  const refinableThreadOptions = useMemo(
    () =>
      threadEntries
        .map(([label]) => String(label || "").trim())
        .filter(Boolean),
    [threadEntries],
  );
  const baseThreadCards = useMemo(() => {
    const provided = summary?.thread_overview?.threads;
    if (Array.isArray(provided) && provided.length) {
      return provided;
    }
    return buildThreadOverviewFallback(threadsMap);
  }, [summary, threadsMap]);
  const threadCards = baseThreadCards;
  const allThreadLabelLookup = useMemo(() => {
    const map = new Map();
    threadCards.forEach((card) => {
      const label = String(card?.label || "").trim();
      if (!label) return;
      map.set(label.toLowerCase(), label);
    });
    Object.keys(threadsMap || {}).forEach((label) => {
      const cleanLabel = String(label || "").trim();
      if (!cleanLabel) return;
      if (!map.has(cleanLabel.toLowerCase())) {
        map.set(cleanLabel.toLowerCase(), cleanLabel);
      }
    });
    return map;
  }, [threadCards, threadsMap]);

  // Resolve parent + optional subthread focus from URL state.
  const parentThreadLabel = activeThread || "";
  const parentCard = parentThreadLabel
    ? threadCards.find((card) => card?.label === parentThreadLabel) || null
    : null;
  const selectedSubthreadPath = useMemo(() => {
    const rawPath = String(searchParams.get("subpath") || "").trim();
    const segments = rawPath
      ? rawPath.split(SUBTHREAD_PATH_DELIMITER)
      : activeSubthread
        ? [activeSubthread]
        : [];
    const seen = new Set();
    if (parentThreadLabel) {
      seen.add(parentThreadLabel.toLowerCase());
    }
    const cleaned = [];
    for (const entry of segments) {
      const nextLabel = String(entry || "").trim();
      if (!nextLabel) continue;
      const canonical = allThreadLabelLookup.get(nextLabel.toLowerCase());
      if (!canonical) continue;
      const lower = canonical.toLowerCase();
      if (seen.has(lower)) continue;
      seen.add(lower);
      cleaned.push(canonical);
      if (cleaned.length >= Math.max(0, MAX_THREAD_DEPTH - 1)) {
        break;
      }
    }
    return cleaned;
  }, [activeSubthread, allThreadLabelLookup, parentThreadLabel, searchParams]);
  const selectedSubthreadLabel = parentCard
    ? selectedSubthreadPath[selectedSubthreadPath.length - 1] || ""
    : "";
  const hasActiveSubthread = Boolean(selectedSubthreadLabel);
  const selectedThreadLabel = selectedSubthreadLabel || parentThreadLabel;
  const selectedCard = selectedThreadLabel
    ? threadCards.find((card) => card?.label === selectedThreadLabel) || null
    : null;
  const generateSubthreadsFor = useCallback(async (threadLabel) => {
    const target = String(threadLabel || selectedThreadLabel || parentThreadLabel || "").trim();
    if (!target) return false;
    setScopeMode("thread");
    setScopeThreadInput(target);
    setSubthreadsOpen(true);
    return generate({ scopeMode: "thread", scopeThread: target });
  }, [generate, parentThreadLabel, selectedThreadLabel]);
  const selectedItems = selectedThreadLabel && Array.isArray(threadsMap?.[selectedThreadLabel])
    ? threadsMap[selectedThreadLabel]
    : [];
  const groupedMentions = useMemo(
    () => groupThreadMentionsByConversation(selectedItems),
    [selectedItems],
  );
  const selectedThreadMissing = Boolean(activeThread) && !parentCard;

  const tagCounts = summary?.tag_counts || {};
  const clusters = summary?.clusters || {};
  const conversations = summary?.conversations || {};
  const selectedK =
    Number(summary?.metadata?.ui_hints?.k_selected)
    || Number(summary?.cluster_count || 0)
    || null;
  const selectedPreferredK = Number(summary?.metadata?.ui_hints?.preferred_k) || null;
  const selectedMaxK = Number(summary?.metadata?.ui_hints?.max_k) || null;
  const generatedScopeMode = summary?.metadata?.ui_hints?.scope_mode || "all";
  const generatedScopeFolder = summary?.metadata?.ui_hints?.scope_folder || "";
  const generatedScopeThread = summary?.metadata?.ui_hints?.scope_thread || "";
  const generatedInferTopics = summary?.metadata?.ui_hints?.infer_topics;
  const generatedKMode = summary?.metadata?.ui_hints?.k_option || "auto";
  const generatedCoalesce = summary?.metadata?.ui_hints?.coalesce_related;
  const mergedLabelCount = Number(summary?.metadata?.ui_hints?.merged_label_count || 0);
  const generatedAtRaw =
    summary?.metadata?.generated_at_utc
    || summary?.metadata?.ui_hints?.generated_at_utc
    || "";
  const generatedAtLabel = formatTimestampLabel(generatedAtRaw);
  const generatedSae = summary?.metadata?.ui_hints?.experimental_sae || null;
  const generatedSignalMode = normalizeThreadSignalMode(
    summary?.metadata?.ui_hints?.thread_signal_mode
    || generatedSae?.retrieval_mode
    || "embeddings",
  );
  const generatedSignalBlend = normalizeThreadSignalBlend(
    summary?.metadata?.ui_hints?.thread_signal_blend
    ?? generatedSae?.retrieval_blend,
    0.7,
  );
  const generatedSaeCombo =
    summary?.metadata?.ui_hints?.sae_model_combo
    || generatedSae?.model_combo
    || "";
  const generatedSaeFallback =
    typeof summary?.metadata?.ui_hints?.sae_embeddings_fallback === "boolean"
      ? summary.metadata.ui_hints.sae_embeddings_fallback
      : typeof generatedSae?.embeddings_fallback === "boolean"
        ? generatedSae.embeddings_fallback
        : true;
  const generationSummary =
    generatedKMode === "auto"
      ? `auto target ${selectedPreferredK || "-"} / max ${selectedMaxK || "-"}`
      : `fixed k ${generatedKMode}`;
  const generatedScopeLabel =
    generatedScopeMode === "folder" && generatedScopeFolder
      ? `folder:${generatedScopeFolder}`
      : generatedScopeMode === "thread" && generatedScopeThread
        ? `thread:${generatedScopeThread}`
        : generatedScopeMode;
  const propertiesSummary = [
    `scope ${generatedScopeLabel}`,
    `infer ${generatedInferTopics === false ? "off" : "on"}`,
    `merge ${generatedCoalesce === false ? "off" : "on"}`,
    mergedLabelCount > 0 ? `merged labels:${mergedLabelCount}` : "",
  ]
    .filter(Boolean)
    .join(" | ");
  const saeSummary = generatedSae?.enabled
    ? `${generatedSae.mode || "inspect"} L${generatedSae.layer ?? "-"} top-k ${generatedSae.topk ?? "-"}`
    : "off";
  const signalSummary = [
    generatedSignalMode,
    generatedSignalMode === "hybrid" ? `sae-weight:${Math.round(generatedSignalBlend * 100)}%` : "",
    generatedSaeFallback ? "embed-fallback:on" : "embed-fallback:off",
    generatedSaeCombo ? `combo:${generatedSaeCombo}` : "",
  ]
    .filter(Boolean)
    .join(" | ");
  const hasSearchQuery = Boolean(searchQ.trim());
  const showNoSearchResults =
    searchAttempted && !searching && hasSearchQuery && searchRes.length === 0;
  const activeConversationFocus = focusedConversation || activeConversationParam;
  const selectedCardIndex = selectedCard
    ? Math.max(
      threadCards.findIndex((card) => card?.label === selectedCard?.label),
      0,
    )
    : 0;
  const selectedTone = selectedCard
    ? getThreadTone(selectedCard?.palette_index ?? selectedCardIndex)
    : null;
  const selectedToneStyle = selectedTone
    ? {
      "--threads-selected-bg": selectedTone.bg,
      "--threads-selected-border": selectedTone.border,
      "--threads-selected-text": selectedTone.text,
    }
    : undefined;
  const parentCardIndex = parentCard
    ? Math.max(
      threadCards.findIndex((card) => card?.label === parentCard?.label),
      0,
    )
    : 0;
  const parentTone = parentCard
    ? getThreadTone(parentCard?.palette_index ?? parentCardIndex)
    : null;
  const parentToneStyle = parentTone
    ? {
      "--threads-selected-bg": parentTone.bg,
      "--threads-selected-border": parentTone.border,
      "--threads-selected-text": parentTone.text,
    }
    : undefined;
  const topActiveThreadLabel = hasActiveSubthread ? parentThreadLabel : selectedThreadLabel;
  const threadToneByLabel = useMemo(() => {
    const map = new Map();
    threadCards.forEach((thread, index) => {
      const label = String(thread?.label || "").trim();
      if (!label) return;
      map.set(label, getThreadTone(thread?.palette_index ?? index));
    });
    return map;
  }, [threadCards]);
  const summaryStats = useMemo(
    () => [
      { label: "threads", value: threadCards.length },
      { label: "conversations", value: Object.keys(conversations).length },
      { label: "k selected", value: selectedK || "-" },
    ],
    [conversations, selectedK, threadCards.length],
  );
  const suggestedManualTopics = useMemo(() => {
    const topicCounts = new Map();
    Object.values(conversations || {}).forEach((info) => {
      const topics =
        info && typeof info === "object" && info.topics && typeof info.topics === "object"
          ? info.topics
          : {};
      Object.entries(topics).forEach(([label, count]) => {
        const cleanLabel = String(label || "").trim();
        const numericCount = Number(count || 0);
        if (!cleanLabel || numericCount <= 0) return;
        topicCounts.set(cleanLabel, (topicCounts.get(cleanLabel) || 0) + numericCount);
      });
    });
    const rankedTopics = Array.from(topicCounts.entries())
      .sort(
        (left, right) =>
          Number(right[1] || 0) - Number(left[1] || 0)
          || String(left[0] || "").localeCompare(String(right[0] || "")),
      )
      .slice(0, 10)
      .map(([label]) => label);
    if (rankedTopics.length) {
      return rankedTopics;
    }
    return threadCards
      .map((thread) => String(thread?.label || "").trim())
      .filter(Boolean)
      .slice(0, 10);
  }, [conversations, threadCards]);
  const suggestManualTopics = useCallback(() => {
    if (!suggestedManualTopics.length) return;
    setManualThreads(suggestedManualTopics.join(", "));
  }, [suggestedManualTopics]);
  const handleSubthreadSearchChange = useCallback((depth, value) => {
    setSubthreadSearchByDepth((prev) => ({
      ...prev,
      [depth]: value,
    }));
  }, []);
  const clearSubthreadSearch = useCallback((depth) => {
    setSubthreadSearchByDepth((prev) => {
      if (!prev[depth]) return prev;
      const next = { ...prev };
      delete next[depth];
      return next;
    });
  }, []);
  const autoTopicBundles = useMemo(() => {
    const currentThreadLabels = threadCards
      .map((thread) => String(thread?.label || "").trim())
      .filter(Boolean)
      .slice(0, 16);
    return [
      {
        id: "auto:suggested",
        name: "Auto: suggested topics",
        topics: suggestedManualTopics,
      },
      {
        id: "auto:current-threads",
        name: "Auto: current thread labels",
        topics: currentThreadLabels,
      },
    ].filter((bundle) => Array.isArray(bundle.topics) && bundle.topics.length);
  }, [suggestedManualTopics, threadCards]);
  const availableTopicBundles = useMemo(
    () => [...autoTopicBundles, ...sanitizeTopicBundles(topicBundles)],
    [autoTopicBundles, topicBundles],
  );
  const selectedTopicBundle = useMemo(
    () => availableTopicBundles.find((bundle) => bundle.id === selectedBundleId) || null,
    [availableTopicBundles, selectedBundleId],
  );

  useEffect(() => {
    if (scopeMode !== "thread") return;
    if (!refinableThreadOptions.length) return;
    const current = String(scopeThreadInput || "").trim();
    if (current && refinableThreadOptions.includes(current)) return;
    const preferredFromActive = activeThread && refinableThreadOptions.includes(activeThread)
      ? activeThread
      : refinableThreadOptions[0];
    if (preferredFromActive && preferredFromActive !== current) {
      setScopeThreadInput(preferredFromActive);
    }
  }, [activeThread, refinableThreadOptions, scopeMode, scopeThreadInput]);

  useEffect(() => {
    setSubthreadSearchByDepth({});
  }, [parentThreadLabel]);

  useEffect(() => {
    if (!availableTopicBundles.length) {
      if (selectedBundleId) {
        setSelectedBundleId("");
      }
      return;
    }
    if (!availableTopicBundles.some((bundle) => bundle.id === selectedBundleId)) {
      setSelectedBundleId(availableTopicBundles[0]?.id || "");
    }
  }, [availableTopicBundles, selectedBundleId]);

  const applyTopicBundle = useCallback((mode = "replace") => {
    if (!selectedTopicBundle?.topics?.length) return;
    const bundleTopics = selectedTopicBundle.topics;
    if (mode === "append") {
      setManualThreads((prev) => mergeTopicLists(parseCommaSeparatedTopics(prev), bundleTopics).join(", "));
      return;
    }
    setManualThreads(bundleTopics.join(", "));
  }, [selectedTopicBundle]);

  const saveManualTopicsAsBundle = useCallback(() => {
    const topics = parseCommaSeparatedTopics(manualThreads);
    if (!topics.length) return;
    const fallbackName = `Bundle ${topicBundles.length + 1}`;
    const name = String(bundleNameInput || fallbackName).trim();
    if (!name) return;
    setTopicBundles((prev) => {
      const existingIndex = prev.findIndex(
        (bundle) => String(bundle?.name || "").trim().toLowerCase() === name.toLowerCase(),
      );
      if (existingIndex >= 0) {
        const next = [...prev];
        const existing = next[existingIndex];
        next[existingIndex] = {
          ...existing,
          topics: mergeTopicLists(existing?.topics || [], topics),
        };
        return next;
      }
      return [
        ...prev,
        {
          id: `bundle-${Date.now()}-${Math.round(Math.random() * 1000)}`,
          name,
          topics,
        },
      ];
    });
    setBundleNameInput("");
  }, [bundleNameInput, manualThreads, topicBundles.length]);

  const threadMetricsByLabel = useMemo(() => {
    const raw = new Map();
    let maxItemCount = 1;
    let minLatestTs = Number.POSITIVE_INFINITY;
    let maxLatestTs = Number.NEGATIVE_INFINITY;

    threadEntries.forEach(([label, rawItems]) => {
      const cleanLabel = String(label || "").trim();
      if (!cleanLabel) return;
      const items = Array.isArray(rawItems) ? rawItems : [];
      const itemCount = items.length;
      let latestTs = null;
      items.forEach((item) => {
        const ts = parseDateToTimestamp(item?.date);
        if (ts === null) return;
        latestTs = latestTs === null ? ts : Math.max(latestTs, ts);
      });
      raw.set(cleanLabel, { itemCount, latestTs });
      maxItemCount = Math.max(maxItemCount, itemCount);
      if (latestTs !== null) {
        minLatestTs = Math.min(minLatestTs, latestTs);
        maxLatestTs = Math.max(maxLatestTs, latestTs);
      }
    });

    const hasRecencySpread =
      Number.isFinite(minLatestTs)
      && Number.isFinite(maxLatestTs)
      && maxLatestTs > minLatestTs;
    const recencySpan = hasRecencySpread ? maxLatestTs - minLatestTs : 0;
    const normalized = new Map();
    raw.forEach((value, label) => {
      const itemNorm = value.itemCount > 0 ? value.itemCount / maxItemCount : 0;
      const recencyNorm =
        value.latestTs === null
          ? 0
          : hasRecencySpread
            ? (value.latestTs - minLatestTs) / recencySpan
            : 1;
      normalized.set(label, {
        ...value,
        itemNorm: Number(itemNorm.toFixed(6)),
        recencyNorm: Number(recencyNorm.toFixed(6)),
      });
    });
    return normalized;
  }, [threadEntries]);

  const conversationTopicsByName = useMemo(() => {
    const map = new Map();
    Object.entries(conversations || {}).forEach(([name, info]) => {
      const normalizedConversation = normalizeConversationName(name);
      if (!normalizedConversation) return;
      const topics =
        info && typeof info === "object" && info.topics && typeof info.topics === "object"
          ? info.topics
          : {};
      map.set(normalizedConversation, topics);
    });
    return map;
  }, [conversations]);

  const getRelatedThreads = useCallback(
    (conversationName) => {
      const normalizedConversation = normalizeConversationName(conversationName);
      if (!normalizedConversation) return [];
      const topicMap = conversationTopicsByName.get(normalizedConversation);
      if (!topicMap || typeof topicMap !== "object") return [];
      const selectedLower = String(selectedThreadLabel || "").trim().toLowerCase();
      const candidates = Object.entries(topicMap)
        .map(([label, count]) => ({
          label: String(label || "").trim(),
          count: Number(count || 0),
        }))
        .filter((entry) => entry.label && entry.count > 0)
        .filter((entry) => entry.label.toLowerCase() !== selectedLower);
      if (!candidates.length) return [];

      const maxLocalCount = candidates.reduce(
        (max, entry) => Math.max(max, Number(entry.count || 0)),
        1,
      );

      return candidates
        .map((entry) => {
          const metrics = threadMetricsByLabel.get(entry.label) || {
            itemNorm: 0,
            recencyNorm: 0,
          };
          const localNorm = Number(entry.count || 0) / maxLocalCount;
          const weightedScore =
            (localNorm * 0.6)
            + ((metrics.itemNorm || 0) * 0.25)
            + ((metrics.recencyNorm || 0) * 0.15);
          return {
            ...entry,
            localNorm: Number(localNorm.toFixed(6)),
            weightedScore: Number(weightedScore.toFixed(6)),
          };
        })
        .sort(
          (left, right) =>
            Number(right.weightedScore || 0) - Number(left.weightedScore || 0)
            || Number(right.count || 0) - Number(left.count || 0)
            || String(left.label || "").localeCompare(String(right.label || "")),
        )
        .slice(0, 6);
    },
    [conversationTopicsByName, selectedThreadLabel, threadMetricsByLabel],
  );

  const getThreadBlipStyle = useCallback(
    (threadLabel) => {
      const tone = threadToneByLabel.get(String(threadLabel || "").trim());
      if (!tone) return undefined;
      return {
        "--thread-blip-bg": tone.bg,
        "--thread-blip-border": tone.border,
        "--thread-blip-text": tone.text,
      };
    },
    [threadToneByLabel],
  );

  const threadCardByLabel = useMemo(() => {
    const map = new Map();
    threadCards.forEach((card, index) => {
      const label = String(card?.label || "").trim();
      if (!label) return;
      map.set(label, {
        ...card,
        palette_index: Number.isInteger(card?.palette_index)
          ? card.palette_index
          : index,
      });
    });
    return map;
  }, [threadCards]);
  const fallbackThreadCardByLabel = useMemo(() => {
    const map = new Map();
    buildThreadOverviewFallback(threadsMap).forEach((card, index) => {
      const label = String(card?.label || "").trim();
      if (!label) return;
      map.set(label, {
        ...card,
        palette_index: Number.isInteger(card?.palette_index)
          ? card.palette_index
          : index,
      });
    });
    return map;
  }, [threadsMap]);
  const getRankedThreadCandidates = useCallback((entries) => {
    if (!Array.isArray(entries) || !entries.length) return [];
    const maxLocalCount = entries.reduce(
      (max, entry) => Math.max(max, Number(entry?.count || 0)),
      1,
    );
    return entries
      .map((entry) => {
        const label = String(entry?.label || "").trim();
        const metrics = threadMetricsByLabel.get(label) || {
          itemNorm: 0,
          recencyNorm: 0,
        };
        const localNorm = Number(entry?.count || 0) / maxLocalCount;
        const weightedScore =
          (localNorm * 0.6)
          + ((metrics.itemNorm || 0) * 0.25)
          + ((metrics.recencyNorm || 0) * 0.15);
        return {
          ...entry,
          localNorm: Number(localNorm.toFixed(6)),
          weightedScore: Number(weightedScore.toFixed(6)),
        };
      })
      .sort(
        (left, right) =>
          Number(right.weightedScore || 0) - Number(left.weightedScore || 0)
          || Number(right.count || 0) - Number(left.count || 0)
          || String(left.label || "").localeCompare(String(right.label || "")),
      );
  }, [threadMetricsByLabel]);
  const getSubthreadCandidatesForParent = useCallback((parentLabel, blockedLabels = []) => {
    const items = Array.isArray(threadsMap?.[parentLabel]) ? threadsMap[parentLabel] : [];
    if (!items.length) return [];
    const blocked = new Set(
      [parentLabel, ...blockedLabels]
        .map((label) => String(label || "").trim().toLowerCase())
        .filter(Boolean),
    );
    const counts = new Map();
    groupThreadMentionsByConversation(items).forEach((row) => {
      const normalizedConversation = normalizeConversationName(row?.conversation);
      if (!normalizedConversation) return;
      const topics = conversationTopicsByName.get(normalizedConversation);
      if (!topics || typeof topics !== "object") return;
      Object.entries(topics).forEach(([label, count]) => {
        const cleanLabel = String(label || "").trim();
        const numericCount = Number(count || 0);
        if (!cleanLabel || numericCount <= 0) return;
        if (blocked.has(cleanLabel.toLowerCase())) return;
        counts.set(cleanLabel, (counts.get(cleanLabel) || 0) + numericCount);
      });
    });
    return getRankedThreadCandidates(
      Array.from(counts.entries()).map(([label, count]) => ({ label, count })),
    ).slice(0, 12);
  }, [conversationTopicsByName, getRankedThreadCandidates, threadsMap]);
  const subthreadDepthPanels = useMemo(() => {
    if (!summary || !parentCard || !parentThreadLabel) return [];
    const panels = [];
    const ancestry = [parentThreadLabel];
    for (let depthIndex = 0; depthIndex < Math.max(0, MAX_THREAD_DEPTH - 1); depthIndex += 1) {
      const depth = depthIndex + 2;
      const parentLabel = depthIndex === 0 ? parentThreadLabel : selectedSubthreadPath[depthIndex - 1];
      if (!parentLabel) break;
      const query = String(subthreadSearchByDepth[depth] || "").trim().toLowerCase();
      const cards = getSubthreadCandidatesForParent(parentLabel, ancestry)
        .map((candidate, index) => {
          const label = String(candidate?.label || "").trim();
          if (!label) return null;
          const card = threadCardByLabel.get(label) || fallbackThreadCardByLabel.get(label);
          if (!card) return null;
          const messageCount = Number(card?.message_count || 0);
          if (messageCount < MIN_SUBTHREAD_MESSAGE_COUNT) return null;
          return {
            ...card,
            item_count: Math.max(Number(card?.item_count || 0), Number(candidate?.count || 0)),
            mention_count: Number(candidate?.count || 0),
            palette_index: Number.isInteger(card?.palette_index)
              ? card.palette_index
              : index,
          };
        })
        .filter(Boolean)
        .filter((card) => {
          if (!query) return true;
          return String(card?.label || "").toLowerCase().includes(query);
        });
      panels.push({
        depth,
        parentLabel,
        selectedLabel: selectedSubthreadPath[depthIndex] || "",
        query,
        cards,
      });
      if (!selectedSubthreadPath[depthIndex]) {
        break;
      }
      ancestry.push(selectedSubthreadPath[depthIndex]);
    }
    return panels;
  }, [
    fallbackThreadCardByLabel,
    getSubthreadCandidatesForParent,
    parentCard,
    parentThreadLabel,
    selectedSubthreadPath,
    subthreadSearchByDepth,
    summary,
    threadCardByLabel,
  ]);
  const conversationSummaryByName = useMemo(() => {
    const map = new Map();
    groupedMentions.forEach((row) => {
      const normalized = normalizeConversationName(row?.conversation);
      if (!normalized) return;
      map.set(normalized, row);
    });
    return map;
  }, [groupedMentions]);
  const selectedTopExampleKeys = useMemo(
    () => new Set((selectedCard?.top_examples || []).map((example) => getSnippetKey(example))),
    [selectedCard],
  );
  // Build snippet rows and apply sort mode (top snippets first, then date/similarity/custom).
  const selectedSnippets = useMemo(() => {
    const rows = (Array.isArray(selectedItems) ? selectedItems : []).map((item, sourceIndex) => {
      const parsedMessageIndex = Number(item?.message_index);
      const messageIndex =
        Number.isInteger(parsedMessageIndex) && parsedMessageIndex >= 0
          ? parsedMessageIndex
          : null;
      const parsedScore = Number(item?.score);
      const score = Number.isFinite(parsedScore) ? parsedScore : null;
      const normalizedConversation = normalizeConversationName(item?.conversation) || "(unknown)";
      const key = getSnippetKey({
        conversation: normalizedConversation,
        message_index: messageIndex,
        date: item?.date,
        excerpt: item?.excerpt,
      });
      return {
        key,
        conversation: normalizedConversation,
        messageIndex,
        date: String(item?.date || ""),
        dateTs: parseDateToTimestamp(item?.date),
        score,
        excerpt: String(item?.excerpt || ""),
        sourceIndex,
        isTopExample: selectedTopExampleKeys.has(key),
      };
    });

    // Deprecated the separate "scroll mentions" panel: all entries are now one snippets list.
    const sorted = [...rows].sort((left, right) => {
      if (left.isTopExample !== right.isTopExample) {
        return Number(right.isTopExample) - Number(left.isTopExample);
      }
      if (snippetSortMode === "custom") {
        return (
          Number(left.sourceIndex || 0) - Number(right.sourceIndex || 0)
          || String(left.conversation || "").localeCompare(String(right.conversation || ""))
        );
      }
      if (snippetSortMode === "similarity") {
        return (
          Number(right.score ?? -1) - Number(left.score ?? -1)
          || Number(right.dateTs || 0) - Number(left.dateTs || 0)
          || Number(left.sourceIndex || 0) - Number(right.sourceIndex || 0)
        );
      }
      return (
        Number(right.dateTs || 0) - Number(left.dateTs || 0)
        || Number(right.score ?? -1) - Number(left.score ?? -1)
        || Number(left.sourceIndex || 0) - Number(right.sourceIndex || 0)
      );
    });

    return sorted;
  }, [selectedItems, selectedTopExampleKeys, snippetSortMode]);
  const activeSnippetSortOption = useMemo(
    () => SNIPPET_SORT_OPTIONS.find((option) => option.id === snippetSortMode) || SNIPPET_SORT_OPTIONS[0],
    [snippetSortMode],
  );
  const toggleSnippetSortMode = useCallback(() => {
    setSnippetSortMode((prev) => {
      const currentIndex = SNIPPET_SORT_MODE_ORDER.indexOf(prev);
      const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % SNIPPET_SORT_MODE_ORDER.length;
      return SNIPPET_SORT_MODE_ORDER[nextIndex];
    });
  }, []);
  const urlSelectedSnippetKey = useMemo(() => {
    if (!activeConversationParam) return "";
    const matchedIndex = selectedSnippets.findIndex((snippet) =>
      snippet.conversation === activeConversationParam
      && (activeMessageParam === null || snippet.messageIndex === activeMessageParam));
    if (matchedIndex < 0) return "";
    const snippet = selectedSnippets[matchedIndex];
    return `${snippet.key}-${matchedIndex}`;
  }, [activeConversationParam, activeMessageParam, selectedSnippets]);
  const effectiveSelectedSnippetKey = selectedSnippetKey
    || urlSelectedSnippetKey
    || (selectedSnippets[0] ? `${selectedSnippets[0].key}-0` : "");
  const selectedConversationRow = activeConversationParam
    ? conversationSummaryByName.get(activeConversationParam) || null
    : null;
  const selectedConversationFallbackReference = selectedConversationRow?.references?.find((reference) => {
    const parsedIndex = Number(reference?.message_index);
    return Number.isInteger(parsedIndex) && parsedIndex >= 0;
  });
  const selectedConversationFallbackIndex = selectedConversationFallbackReference
    ? Number(selectedConversationFallbackReference.message_index)
    : 0;
  const activeConversationMessages = activeConversationParam
    && Array.isArray(conversationCache[activeConversationParam])
    ? conversationCache[activeConversationParam]
    : [];
  const resolvedInlineMessageIndex = Number.isInteger(activeMessageParam)
    ? activeMessageParam
    : selectedConversationFallbackIndex;
  const clampedInlineMessageIndex = activeConversationMessages.length
    ? Math.max(0, Math.min(activeConversationMessages.length - 1, resolvedInlineMessageIndex))
    : resolvedInlineMessageIndex;

  useEffect(() => {
    if (!activeConversationParam) return;
    ensureConversationLoaded(activeConversationParam);
  }, [activeConversationParam, ensureConversationLoaded]);

  useEffect(() => {
    if (!activeConversationParam || activeMessageParam === null) return;
    const key = `${activeConversationParam}:${activeMessageParam}`;
    const node = inlineMessageRefs.current.get(key);
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "center" });
    }
  }, [activeConversationParam, activeMessageParam, conversationCache]);

  useEffect(() => {
    setFocusedConversation("");
  }, [selectedThreadLabel]);

  useEffect(() => {
    if (!selectedThreadLabel) {
      setSelectedSnippetKey("");
      return;
    }
    setSelectedSnippetKey((prev) => {
      if (!prev) return "";
      return selectedSnippets.some((snippet, index) => `${snippet.key}-${index}` === prev) ? prev : "";
    });
  }, [selectedSnippets, selectedThreadLabel]);

  const setConversationHover = (conversationName) => {
    setFocusedConversation(normalizeConversationName(conversationName));
  };

  const clearConversationHover = () => {
    setFocusedConversation("");
  };

  const animateGalleryWheel = useCallback(() => {
    const strip = galleryStripRef.current;
    if (!strip) return;
    const targetLeft = galleryWheelTargetRef.current;
    const delta = targetLeft - strip.scrollLeft;
    if (Math.abs(delta) < 0.75) {
      strip.scrollLeft = targetLeft;
      galleryWheelFrameRef.current = 0;
      return;
    }
    strip.scrollLeft += delta * 0.24;
    if (typeof window !== "undefined") {
      galleryWheelFrameRef.current = window.requestAnimationFrame(animateGalleryWheel);
    }
  }, []);

  const queueGalleryWheel = useCallback((deltaX) => {
    const strip = galleryStripRef.current;
    if (!strip) return;
    const maxLeft = Math.max(0, strip.scrollWidth - strip.clientWidth);
    const from = galleryWheelFrameRef.current ? galleryWheelTargetRef.current : strip.scrollLeft;
    galleryWheelTargetRef.current = Math.max(0, Math.min(maxLeft, from + deltaX));
    if (!galleryWheelFrameRef.current && typeof window !== "undefined") {
      galleryWheelFrameRef.current = window.requestAnimationFrame(animateGalleryWheel);
    }
  }, [animateGalleryWheel]);

  const handleGalleryWheel = useCallback((event) => {
    const strip = galleryStripRef.current;
    if (!strip) return;
    const target = event.target;
    if (
      target instanceof Element
      && target.closest(
        ".threads-inline-scroll,.threads-snippets-list,.threads-subthreads-pane",
      )
    ) {
      return;
    }
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    event.preventDefault();
    queueGalleryWheel(event.deltaY * 1.15);
  }, [queueGalleryWheel]);

  useEffect(() => () => {
    if (!galleryWheelFrameRef.current || typeof window === "undefined") return;
    window.cancelAnimationFrame(galleryWheelFrameRef.current);
    galleryWheelFrameRef.current = 0;
  }, []);

  const registerGalleryCardRef = useCallback(
    (threadLabel) => (node) => {
      const label = String(threadLabel || "").trim();
      if (!label) return;
      if (node) {
        galleryCardRefs.current.set(label, node);
      } else {
        galleryCardRefs.current.delete(label);
      }
    },
    [],
  );

  const centerGalleryCard = useCallback((threadLabel, behavior = "smooth") => {
    const label = String(threadLabel || "").trim();
    if (!label) return;
    const strip = galleryStripRef.current;
    const card = galleryCardRefs.current.get(label);
    if (!strip || !card) return;
    const nextLeft = card.offsetLeft - ((strip.clientWidth - card.clientWidth) / 2);
    const maxLeft = Math.max(0, strip.scrollWidth - strip.clientWidth);
    const clampedLeft = Math.max(0, Math.min(maxLeft, nextLeft));
    galleryWheelTargetRef.current = clampedLeft;
    if (typeof strip.scrollTo === "function") {
      strip.scrollTo({ left: clampedLeft, behavior });
    } else {
      strip.scrollLeft = clampedLeft;
    }
  }, []);

  useEffect(() => {
    if (!selectedThreadLabel) return;
    if (typeof window === "undefined") {
      centerGalleryCard(selectedThreadLabel, "auto");
      return;
    }
    const behavior = activeThread ? "smooth" : "auto";
    const timer = window.setTimeout(() => {
      centerGalleryCard(selectedThreadLabel, behavior);
    }, 24);
    return () => window.clearTimeout(timer);
  }, [activeThread, centerGalleryCard, selectedThreadLabel, threadCards.length]);

  const bindInlineMessageRef = useCallback(
    (conversationName, messageIndex) => (node) => {
      const key = `${conversationName}:${messageIndex}`;
      if (node) {
        inlineMessageRefs.current.set(key, node);
      } else {
        inlineMessageRefs.current.delete(key);
      }
    },
    [],
  );

  return (
    <div className="threads-tab">
      <section className={`threads-hero${topBarCollapsed ? " is-collapsed" : ""}`}>
        <div className="threads-hero-main">
          <div className="threads-hero-copy">
            <h2>Threads</h2>
            {summary ? (
              <div className="threads-hero-inline-meta">
                <span className="threads-hero-chip" title="Thread generation setup used for this summary">
                  Generation: {generationSummary}
                </span>
                <span className="threads-hero-chip" title="Scope and behavior settings applied to this run">
                  Properties: {propertiesSummary}
                </span>
                <span className="threads-hero-chip" title="Planned signal path metadata for embeddings/SAE retrieval strategy">
                  Signal path: {signalSummary}
                </span>
                <span
                  className="threads-hero-chip"
                  title={
                    generatedAtRaw
                      ? `UTC timestamp: ${generatedAtRaw}`
                      : "No generation timestamp saved yet"
                  }
                >
                  Last calculated: {generatedAtLabel}
                </span>
                <span className="threads-hero-chip" title="Experimental sparse autoencoder options from the last run">
                  SAE (experimental): {saeSummary}
                </span>
              </div>
            ) : null}
          </div>
          <div className="threads-hero-actions">
            <button type="button" className="threads-btn-primary" onClick={generate} disabled={loading}>
              {loading ? "Generating..." : "Generate threads"}
            </button>
            <button type="button" className="threads-options-btn" onClick={() => setOptionsOpen(true)}>
              Generate options
            </button>
            <button type="button" onClick={loadSummary} disabled={loading}>
              Refresh
            </button>
            <button
              type="button"
              className="threads-subtle-btn threads-hero-toggle threads-hero-toggle-symbol"
              onClick={() => setTopBarCollapsed((prev) => !prev)}
              aria-expanded={!topBarCollapsed}
              aria-label={topBarCollapsed ? "Expand top bar" : "Collapse top bar"}
              title={topBarCollapsed ? "Expand top bar" : "Collapse top bar"}
            >
              {topBarCollapsed ? "+" : "-"}
            </button>
          </div>
        </div>
        {loading ? (
          <div className="threads-generate-progress" role="status" aria-live="polite">
            <div className="threads-generate-progress-track" aria-hidden="true">
              <span className="threads-generate-progress-bar" />
            </div>
            <span>Calculating threads...</span>
          </div>
        ) : null}
        {!topBarCollapsed ? (
          <div className="threads-hero-details">
            <p>
              {summary
                ? `${Object.keys(tagCounts).length} tags in summary. Select a thread card to focus; click outside or use x to clear focus.`
                : "Extract major themes and explore deeper with subthreads."}
            </p>
            {summary ? (
              <div className="threads-hero-summary-row" role="status" aria-label="Thread summary metrics">
                {summaryStats.map((metric) => (
                  <span key={metric.label} className="threads-hero-summary-chip">
                    <strong>{metric.value}</strong> {metric.label}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      {threadCards.length ? (
        <section
          className={`threads-gallery-shell${hasActiveSubthread ? " is-compact-mode" : ""}`}
          onClick={(event) => {
            if (!activeThread) return;
            if (event.target === event.currentTarget) {
              updateThreadParam("", { clearConversation: true, clearSubthread: true });
            }
          }}
        >
          <div
            ref={galleryStripRef}
            className="threads-gallery-strip"
            onWheel={handleGalleryWheel}
            onScroll={() => {
              const strip = galleryStripRef.current;
              if (!strip || galleryWheelFrameRef.current) return;
              galleryWheelTargetRef.current = strip.scrollLeft;
            }}
            onClick={(event) => {
              if (!activeThread) return;
              if (event.target === event.currentTarget) {
                updateThreadParam("", { clearConversation: true, clearSubthread: true });
              }
            }}
          >
            {threadCards.map((thread, index) => {
              const rawLabel = String(thread?.label || "").trim();
              const displayLabel = rawLabel || "(unnamed thread)";
              const tone = getThreadTone(thread?.palette_index ?? index);
              const isActive = rawLabel === topActiveThreadLabel;
              const isContext = hasActiveSubthread && rawLabel === parentThreadLabel;
              return (
                <article
                  key={`gallery-${thread?.id || rawLabel || index}`}
                  ref={registerGalleryCardRef(rawLabel)}
                  className={`threads-gallery-card${isActive ? " is-active is-expanded" : ""}${isContext ? " is-context" : ""}`}
                  style={{
                    "--gallery-thread-bg": tone.bg,
                    "--gallery-thread-border": tone.border,
                    "--gallery-thread-text": tone.text,
                  }}
                  role="button"
                  tabIndex={0}
                  aria-expanded={isActive}
                  onClick={(event) => {
                    const target = event.target;
                    if (
                      target instanceof Element
                      && target.closest("button, a, input, select, textarea, label, summary")
                    ) {
                      return;
                    }
                    if (!rawLabel) return;
                    if (isActive) return;
                    updateThreadParam(rawLabel, {
                      clearConversation: true,
                      clearSubthread: true,
                    });
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    if (!rawLabel) return;
                    if (isActive) return;
                    updateThreadParam(rawLabel, {
                      clearConversation: true,
                      clearSubthread: true,
                    });
                  }}
                >
                  <header className="threads-gallery-head">
                    <div className="threads-gallery-head-row">
                      <button
                        type="button"
                        className="threads-gallery-title"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (!rawLabel) return;
                          if (isActive) return;
                          updateThreadParam(rawLabel, {
                            clearConversation: true,
                            clearSubthread: true,
                          });
                        }}
                        title={`Focus ${displayLabel}`}
                        disabled={!rawLabel}
                      >
                        {displayLabel}
                      </button>
                      {isActive ? (
                        <button
                          type="button"
                          className="threads-card-close"
                          onClick={(event) => {
                            event.stopPropagation();
                            updateThreadParam("", {
                              clearConversation: true,
                              clearSubthread: true,
                            });
                          }}
                          aria-label={`Deselect ${displayLabel}`}
                          title="Deselect thread"
                        >
                          x
                        </button>
                      ) : null}
                    </div>
                    <div className={`threads-gallery-meta-row${isActive ? " is-active" : ""}`}>
                      <div className="threads-gallery-stats">
                        <span>{Number(thread?.conversation_count || 0)} conv</span>
                        <span>{Number(thread?.item_count || 0)} snippets</span>
                        <span>{Number(thread?.message_count || 0)} messages</span>
                      </div>
                      {isActive && !hasActiveSubthread ? (
                        <div className="threads-gallery-tools">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              startRename(thread?.label || "");
                            }}
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setScopeMode("thread");
                              setScopeThreadInput(thread?.label || "");
                              setOptionsOpen(true);
                            }}
                          >
                            Refine
                          </button>
                          <div className="threads-sort-controls" role="group" aria-label="Sort snippets">
                            <button
                              type="button"
                              className="threads-sort-toggle-btn"
                              onClick={(event) => {
                                event.stopPropagation();
                                toggleSnippetSortMode();
                              }}
                              title={`Sort snippets: ${String(activeSnippetSortOption?.label || "Date").toLowerCase()}. Click to cycle.`}
                              aria-label={`Sort snippets: ${String(activeSnippetSortOption?.label || "Date").toLowerCase()}`}
                            >
                              <SwapVertIcon className="threads-sort-toggle-icon" fontSize="inherit" aria-hidden="true" />
                              <span>{activeSnippetSortOption?.label || "Date"}</span>
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </header>
                  {isActive && !hasActiveSubthread ? (
                    <div className="threads-gallery-expanded-content">
                      <section className="threads-focus-panel threads-toned-panel" style={selectedToneStyle}>
                        <div className="threads-examples">
                          <h4>Snippets</h4>
                          {selectedSnippets.length ? (
                            <div className="threads-snippets-list">
                              {selectedSnippets.map((snippet, snippetIndex) => {
                                const conversationRow = conversationSummaryByName.get(snippet.conversation) || null;
                                const relatedThreads = getRelatedThreads(snippet.conversation);
                                const snippetRenderKey = `${snippet.key}-${snippetIndex}`;
                                const isSelectedSnippet = effectiveSelectedSnippetKey === snippetRenderKey;
                                const isLinked =
                                  Boolean(activeConversationFocus)
                                  && snippet.conversation === activeConversationFocus;
                                return (
                                  <article
                                    key={`${snippet.key}-${snippetIndex}`}
                                    className={`threads-example-card threads-snippet-card${
                                      isLinked ? " is-linked" : ""
                                    }${isSelectedSnippet ? " is-selected-snippet" : ""}${
                                      snippet.isTopExample ? " is-top-snippet" : ""
                                    }`}
                                    onMouseEnter={() => setConversationHover(snippet.conversation)}
                                    onMouseLeave={clearConversationHover}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      const target = event.target;
                                      if (target instanceof Element && target.closest("button, a")) return;
                                      setSelectedSnippetKey(snippetRenderKey);
                                      setFocusedConversation(snippet.conversation);
                                    }}
                                  >
                                    <div className="thread-item-meta">
                                      {snippet.date || "n/a"} |{" "}
                                      <button
                                        type="button"
                                        className="threads-inline-link"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          loadConversation(snippet.conversation);
                                        }}
                                        title="Open conversation in chat"
                                      >
                                        {snippet.conversation}
                                      </button>{" "}
                                      #{snippet.messageIndex ?? "?"}
                                      {" | "}
                                      score {snippet.score ?? "-"}
                                      {snippet.isTopExample ? " | top snippet" : ""}
                                    </div>
                                    <p className="threads-conversation-snippet" title={snippet.excerpt}>
                                      {snippet.excerpt || "No excerpt available."}
                                    </p>
                                    <div className="threads-conversation-meta-row">
                                      <span className="thread-item-meta">
                                        {conversationRow
                                          ? `${conversationRow.itemCount} refs | ${conversationRow.messageCount} messages${conversationRow.latestDate ? ` | latest ${conversationRow.latestDate}` : ""}`
                                          : "No conversation stats"}
                                      </span>
                                      {relatedThreads.length ? (
                                        <div className="threads-related-topics threads-related-topics-compact">
                                          {relatedThreads.slice(0, 3).map((topic) => (
                                            <button
                                              type="button"
                                              key={`${snippet.key}-${topic.label}`}
                                              className="threads-topic-blip"
                                              style={getThreadBlipStyle(topic.label)}
                                              onClick={(event) => {
                                                event.stopPropagation();
                                                if (
                                                  parentThreadLabel
                                                  && topic.label.toLowerCase() !== parentThreadLabel.toLowerCase()
                                                ) {
                                                  updateSubthreadParam(topic.label, {
                                                    clearConversation: true,
                                                  });
                                                  return;
                                                }
                                                updateThreadParam(topic.label, {
                                                  clearConversation: true,
                                                  clearSubthread: true,
                                                });
                                              }}
                                              title={`Jump to related thread ${topic.label} (score ${Number(topic.weightedScore || 0).toFixed(2)})`}
                                            >
                                              {topic.label}
                                              <span>{topic.count}</span>
                                            </button>
                                          ))}
                                        </div>
                                      ) : null}
                                    </div>
                                  </article>
                                );
                              })}
                            </div>
                          ) : (
                            <p className="threads-empty-state">No snippets found for this thread.</p>
                          )}
                        </div>

                        {activeConversationParam ? (
                          <div className="threads-inline-conversation">
                            <div className="threads-inline-head">
                              <strong>{activeConversationParam}</strong>
                              <button
                                type="button"
                                className="threads-subtle-btn"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  loadConversation(activeConversationParam);
                                }}
                              >
                                Open full chat
                              </button>
                            </div>
                            {selectedConversationRow ? (
                              <p className="thread-item-meta">
                                {selectedConversationRow.itemCount} refs | {selectedConversationRow.messageCount} messages
                                {selectedConversationRow.latestDate ? ` | latest ${selectedConversationRow.latestDate}` : ""}
                                {selectedConversationRow.avgScore !== null ? ` | avg ${selectedConversationRow.avgScore}` : ""}
                              </p>
                            ) : null}
                            {conversationLoadingKey === activeConversationParam && !activeConversationMessages.length ? (
                              <p className="threads-empty-state">Loading conversation...</p>
                            ) : activeConversationMessages.length ? (
                              <div className="threads-inline-scroll">
                                {activeConversationMessages.map((message, messageIndex) => {
                                  const role = normalizeMessageRole(message);
                                  const roleMarker = formatMessageRole(message);
                                  const isSelectedMessage = messageIndex === clampedInlineMessageIndex;
                                  const isNeighborUp = messageIndex === clampedInlineMessageIndex - 1;
                                  const isNeighborDown = messageIndex === clampedInlineMessageIndex + 1;
                                  return (
                                    <button
                                      type="button"
                                      key={`${activeConversationParam}-inline-${messageIndex}`}
                                      ref={bindInlineMessageRef(activeConversationParam, messageIndex)}
                                      className={`threads-inline-message${
                                        isSelectedMessage ? " is-selected" : ""
                                      }${isNeighborUp ? " is-neighbor-up" : ""}${
                                        isNeighborDown ? " is-neighbor-down" : ""
                                      }`}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        selectConversationFocus(activeConversationParam, messageIndex);
                                      }}
                                    >
                                      <span className="threads-inline-message-meta">
                                        <span
                                          className={`threads-inline-message-role ${
                                            role === "assistant" ? "is-assistant" : "is-user"
                                          }`}
                                        >
                                          {roleMarker}
                                        </span>
                                        {role} #{messageIndex}
                                      </span>
                                      <pre className="pre-wrap">
                                        {extractMessageText(message) || "(no text payload)"}
                                      </pre>
                                    </button>
                                  );
                                })}
                              </div>
                            ) : (
                              <p className="threads-empty-state">No message history available.</p>
                            )}
                          </div>
                        ) : null}
                      </section>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : (
        <p className="threads-empty-state">
          No summary yet. Click Generate threads to run semantic tagging.
        </p>
      )}

      {error ? <div className="threads-error">{error}</div> : null}
      {renameError ? <div className="threads-error">{renameError}</div> : null}

      {selectedThreadMissing ? (
        <p className="threads-empty-state">No threads match the active filter.</p>
      ) : null}

      {summary && parentCard ? (
        <section className="threads-subthreads-shell threads-toned-panel" style={parentToneStyle}>
          <details
            className="threads-subthreads-pane"
            open={subthreadsOpen}
            onToggle={(event) => setSubthreadsOpen(event.currentTarget.open)}
          >
            <summary>
              Thread depth navigator for <strong>{parentCard.label}</strong>
            </summary>
            <p className="threads-subthreads-note">
              Depth 1 stays pinned to {parentCard.label}. Depth 2+ branches only when the
              previous level is focused, and fibers without message bodies stay hidden until
              they have enough material to stand on their own.
            </p>
            <div className="threads-depth-stack">
              {subthreadDepthPanels.map((panel, depthIndex) => {
                const panelBusy =
                  loading
                  && scopeMode === "thread"
                  && String(scopeThreadInput || activeThread || "").trim() === panel.parentLabel;
                return (
                  <section
                    key={`depth-panel-${panel.depth}-${panel.parentLabel}`}
                    className="threads-depth-panel"
                  >
                    <div className="threads-depth-head">
                      <div>
                        <span className="threads-depth-kicker">Depth {panel.depth}</span>
                        <h4>Level {panel.depth} under {panel.parentLabel}</h4>
                      </div>
                      <span className="threads-depth-summary">
                        {panel.selectedLabel
                          ? `Focused: ${panel.selectedLabel}`
                          : `Select a card to unlock depth ${Math.min(panel.depth + 1, MAX_THREAD_DEPTH)}.`}
                      </span>
                    </div>
                    <div className="threads-subthreads-search-row">
                      <input
                        type="search"
                        value={String(subthreadSearchByDepth[panel.depth] || "")}
                        onChange={(event) => handleSubthreadSearchChange(panel.depth, event.target.value)}
                        placeholder={`search depth ${panel.depth}`}
                      />
                      {panel.query ? (
                        <button
                          type="button"
                          className="threads-subtle-btn"
                          onClick={() => clearSubthreadSearch(panel.depth)}
                        >
                          Clear
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="threads-btn-primary"
                        onClick={() => generateSubthreadsFor(panel.parentLabel)}
                        disabled={loading}
                      >
                        {panelBusy ? "Generating..." : "Generate subthreads"}
                      </button>
                      <button
                        type="button"
                        className="threads-subtle-btn"
                        onClick={() => generateSubthreadsFor(panel.parentLabel)}
                        disabled={loading}
                      >
                        {panelBusy ? "Refreshing..." : "Refresh"}
                      </button>
                    </div>
                    {panel.cards.length ? (
                      <section className="threads-gallery-shell threads-gallery-shell-subthreads">
                        <div className="threads-gallery-strip threads-gallery-strip-subthreads">
                          {panel.cards.map((thread, index) => {
                            const rawLabel = String(thread?.label || "").trim();
                            const displayLabel = rawLabel || "(unnamed thread)";
                            const tone = getThreadTone(thread?.palette_index ?? index);
                            const mentionCount = Number(thread?.mention_count || 0);
                            const isActive = rawLabel === panel.selectedLabel;
                            const previewExcerpt = String(thread?.top_examples?.[0]?.excerpt || "").trim();
                            const nextPath = [...selectedSubthreadPath.slice(0, depthIndex), rawLabel];
                            return (
                              <article
                                key={`subthread-gallery-${panel.depth}-${thread?.id || rawLabel || index}`}
                                className={`threads-gallery-card threads-gallery-card-subthread${
                                  isActive ? " is-active is-expanded" : ""
                                }`}
                                style={{
                                  "--gallery-thread-bg": tone.bg,
                                  "--gallery-thread-border": tone.border,
                                  "--gallery-thread-text": tone.text,
                                }}
                                role="button"
                                tabIndex={0}
                                aria-expanded={isActive}
                                onClick={(event) => {
                                  const target = event.target;
                                  if (
                                    target instanceof Element
                                    && target.closest("button, a, input, select, textarea, label, summary")
                                  ) {
                                    return;
                                  }
                                  if (!rawLabel || isActive) return;
                                  updateSubthreadPath(nextPath, { clearConversation: true });
                                }}
                                onKeyDown={(event) => {
                                  if (event.key !== "Enter" && event.key !== " ") return;
                                  event.preventDefault();
                                  if (!rawLabel || isActive) return;
                                  updateSubthreadPath(nextPath, { clearConversation: true });
                                }}
                              >
                                <header className="threads-gallery-head">
                                  <div className="threads-gallery-head-row">
                                    <button
                                      type="button"
                                      className="threads-gallery-title"
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        if (!rawLabel || isActive) return;
                                        updateSubthreadPath(nextPath, { clearConversation: true });
                                      }}
                                      title={`Focus depth ${panel.depth} thread ${displayLabel}`}
                                      disabled={!rawLabel}
                                    >
                                      {displayLabel}
                                    </button>
                                    {isActive ? (
                                      <button
                                        type="button"
                                        className="threads-card-close"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          updateSubthreadPath(
                                            selectedSubthreadPath.slice(0, depthIndex),
                                            { clearConversation: true },
                                          );
                                        }}
                                        aria-label={`Close depth ${panel.depth} thread ${displayLabel}`}
                                        title="Step back one depth"
                                      >
                                        x
                                      </button>
                                    ) : null}
                                  </div>
                                  <div className={`threads-gallery-meta-row${isActive ? " is-active" : ""}`}>
                                    <div className="threads-gallery-stats">
                                      <span>{mentionCount} refs</span>
                                      <span>{Number(thread?.conversation_count || 0)} conv</span>
                                      <span>{Number(thread?.item_count || 0)} snippets</span>
                                      <span>{Number(thread?.message_count || 0)} messages</span>
                                    </div>
                                  </div>
                                </header>
                                <p
                                  className={`threads-gallery-preview${
                                    previewExcerpt ? "" : " is-muted"
                                  }`}
                                  title={previewExcerpt || displayLabel}
                                >
                                  {previewExcerpt
                                    || `Select ${displayLabel} to open depth ${Math.min(panel.depth + 1, MAX_THREAD_DEPTH)}.`}
                                </p>
                              </article>
                            );
                          })}
                        </div>
                      </section>
                    ) : (
                      <p className="threads-empty-state">
                        {panel.query
                          ? `No depth ${panel.depth} threads match "${panel.query}".`
                          : `No depth ${panel.depth} threads are ready yet. Generate subthreads to refine ${panel.parentLabel}.`}
                      </p>
                    )}
                  </section>
                );
              })}
            </div>
          </details>
        </section>
      ) : null}

      <FilterBar
        searchPlaceholder="search by topic"
        searchValue={searchQ}
        onSearch={setSearchQ}
        onSearchSubmit={doSearch}
        right={(
          <div className="threads-search-actions">
            {hasSearchQuery ? (
              <button type="button" className="threads-subtle-btn" onClick={clearSearch}>
                Clear
              </button>
            ) : null}
            <button type="button" onClick={doSearch} disabled={searching || !hasSearchQuery}>
              {searching ? "Searching..." : "Search"}
            </button>
          </div>
        )}
      />

      {searchRes.length ? (
        <section className="threads-search-results">
          <h3>Search results</h3>
          <div className="search-grid">
            {searchRes.map((match, index) => (
              <div key={`${match?.conversation || "conv"}-${index}`} className="search-result-card">
                <div className="thread-item-meta">
                  {match?.date || "n/a"} |{" "}
                  <button
                    type="button"
                    className="threads-inline-link"
                    onClick={() => loadConversation(match?.conversation)}
                    title="Open conversation in chat"
                  >
                    {normalizeConversationName(match?.conversation) || "(unknown)"}
                  </button>{" "}
                  #
                  {match?.message_index ?? "?"}
                  {" | "}
                  score {match?.score ?? "-"}
                </div>
                <pre className="pre-wrap">{match?.excerpt || "No excerpt available."}</pre>
              </div>
            ))}
          </div>
        </section>
      ) : showNoSearchResults ? (
        <p className="threads-empty-state search-empty">
          No search matches for <strong>{searchQ.trim()}</strong>.
        </p>
      ) : null}

      {summary ? (
        <details className="threads-diagnostics">
          <summary>Diagnostics: tags, clusters, and conversation topic map</summary>
          <div className="threads-diagnostics-grid">
            <div className="threads-diagnostics-panel">
              <h4>Tags</h4>
              {Object.keys(tagCounts).length ? (
                <table>
                  <thead>
                    <tr>
                      <th>Tag</th>
                      <th>Count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(tagCounts).map(([tag, count]) => (
                      <tr key={tag}>
                        <td>{tag}</td>
                        <td>{count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="threads-empty-state">No tags yet.</p>
              )}
            </div>
            <div className="threads-diagnostics-panel">
              <h4>Clusters</h4>
              {Object.keys(clusters).length ? (
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Label</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(clusters).map(([id, label]) => (
                      <tr key={id}>
                        <td>{id}</td>
                        <td>{label || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="threads-empty-state">No clusters yet.</p>
              )}
            </div>
            <div className="threads-diagnostics-panel diagnostics-wide">
              <h4>Conversation topic map</h4>
              {Object.keys(conversations).length ? (
                <table>
                  <thead>
                    <tr>
                      <th>Conversation</th>
                      <th>Nuggets</th>
                      <th>Topics</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(conversations)
                      .sort(
                        (left, right) =>
                          Number(right?.[1]?.nugget_count || 0) - Number(left?.[1]?.nugget_count || 0),
                      )
                      .slice(0, 40)
                      .map(([name, info]) => (
                        <tr key={name}>
                          <td>{normalizeConversationName(name)}</td>
                          <td>{Number(info?.nugget_count || 0)}</td>
                          <td>
                            {Object.entries(info?.topics || {}).map(([topic, count]) => (
                              <span key={topic} className="tag-item">
                                {topic} ({count})
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              ) : (
                <p className="threads-empty-state">No conversation map yet.</p>
              )}
            </div>
          </div>
        </details>
      ) : null}

      {optionsOpen ? (
        <div className="threads-modal-overlay" onClick={() => setOptionsOpen(false)}>
          <div
            className="threads-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Thread generation options"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="threads-modal-header">
              <div>
                <p className="threads-section-label">Generate options</p>
                <h3>Build or refine threads</h3>
              </div>
              <button type="button" className="threads-modal-close" onClick={() => setOptionsOpen(false)}>
                x
              </button>
            </div>

            <div className="threads-modal-grid">
              <label className="threads-modal-field inline-label">
                <input
                  type="checkbox"
                  checked={inferTopics}
                  onChange={(event) => setInferTopics(event.target.checked)}
                />
                <span title="When enabled, topics are inferred automatically instead of only using your manual labels.">
                  infer topics automatically
                </span>
              </label>

              <label className="threads-modal-field">
                top-K strategy
                <small className="threads-field-help">
                  K is the cluster count. Auto estimates it; fixed K forces an exact thread count.
                </small>
                <select
                  value={kOption}
                  onChange={(event) => setKOption(event.target.value)}
                  title="Auto calculates cluster count from your data. Fixed K enforces a set number of threads."
                >
                  <option value="auto">auto</option>
                  <option value="4">4</option>
                  <option value="8">8</option>
                  <option value="16">16</option>
                  <option value="32">32</option>
                </select>
              </label>

              {kOption === "auto" ? (
                <>
                  <label className="threads-modal-field">
                    target k
                    <small className="threads-field-help">
                      Preferred cluster target for auto mode.
                    </small>
                    <input
                      type="number"
                      min={2}
                      max={60}
                      value={preferredK}
                      onChange={(event) => setPreferredK(event.target.value)}
                    />
                  </label>
                  <label className="threads-modal-field">
                    max k
                    <small className="threads-field-help">
                      Upper bound auto mode will not exceed.
                    </small>
                    <input
                      type="number"
                      min={2}
                      max={80}
                      value={maxK}
                      onChange={(event) => setMaxK(event.target.value)}
                    />
                  </label>
                </>
              ) : null}

              <label className="threads-modal-field inline-label">
                <input
                  type="checkbox"
                  checked={coalesceRelated}
                  onChange={(event) => setCoalesceRelated(event.target.checked)}
                />
                <span title="Merges near-duplicate labels such as singular/plural or tiny wording changes.">
                  merge related labels
                </span>
              </label>

              <label className="threads-modal-field threads-modal-field-half">
                top threads to keep
                <small className="threads-field-help">
                  Keep only the strongest N discovered threads in the final gallery.
                </small>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={topN}
                  onChange={(event) => setTopN(event.target.value)}
                  title="Number of strongest discovered threads kept in the final output."
                />
              </label>

              <label className="threads-modal-field threads-modal-field-half">
                scope
                <small className="threads-field-help">
                  Refine all conversations, one folder path, or an existing thread into subthreads.
                </small>
                <select
                  value={scopeMode}
                  onChange={(event) => setScopeMode(event.target.value)}
                  title="Choose generation scope: all conversations, folder path, or a thread group."
                >
                  <option value="all">all conversations</option>
                  <option value="folder">folder</option>
                  <option value="thread">thread group</option>
                </select>
              </label>

              {scopeMode === "folder" ? (
                <label className="threads-modal-field field-wide">
                  folder scope
                  <input
                    type="text"
                    placeholder="projects/events"
                    value={scopeFolder}
                    onChange={(event) => setScopeFolder(event.target.value)}
                  />
                </label>
              ) : null}

              {scopeMode === "thread" ? (
                <label
                  className="threads-modal-field threads-modal-field-half"
                  title="Select an existing thread group to split into subthreads."
                >
                  thread to refine
                  <small className="threads-field-help">
                    Auto-populates from existing thread groups in this summary.
                  </small>
                  <select
                    value={scopeThreadInput}
                    onChange={(event) => setScopeThreadInput(event.target.value)}
                    title="Choose which existing thread group to refine."
                    disabled={!refinableThreadOptions.length}
                  >
                    {refinableThreadOptions.length ? (
                      refinableThreadOptions.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))
                    ) : (
                      <option value="">no thread groups available</option>
                    )}
                  </select>
                  {!refinableThreadOptions.length ? (
                    <small className="threads-field-help">
                      Generate threads once first so thread groups can be selected here.
                    </small>
                  ) : null}
                </label>
              ) : null}

              <label className="threads-modal-field field-wide">
                seed tags (comma separated)
                <small className="threads-field-help">
                  Optional bias terms for automatic discovery. Threads can still be inferred beyond these.
                </small>
                <input
                  type="text"
                  value={customTags}
                  onChange={(event) => setCustomTags(event.target.value)}
                  placeholder="planning, menu, logistics"
                  title="Optional guidance terms; model can still discover additional threads."
                />
              </label>

              <label className="threads-modal-field field-wide">
                manual thread labels (comma separated)
                <small className="threads-field-help">
                  Pre-coded topic buckets. If set, mentions map directly to these labels.
                </small>
                <div className="threads-manual-topics-row">
                  <input
                    type="text"
                    value={manualThreads}
                    onChange={(event) => setManualThreads(event.target.value)}
                    placeholder="Action items, recipe ideas"
                    title="Manual labels become fixed target topics for assignment."
                  />
                  <button
                    type="button"
                    className="threads-subtle-btn"
                    onClick={suggestManualTopics}
                    disabled={!suggestedManualTopics.length}
                    title="Fill from a high-level scan of current conversation topic stats."
                  >
                    Suggest topics
                  </button>
                </div>
                <div className="threads-bundle-row">
                  <select
                    value={selectedBundleId}
                    onChange={(event) => setSelectedBundleId(event.target.value)}
                    title="Saved and automatic topic bundles"
                  >
                    {availableTopicBundles.length ? (
                      availableTopicBundles.map((bundle) => (
                        <option key={bundle.id} value={bundle.id}>
                          {bundle.name} ({bundle.topics.length})
                        </option>
                      ))
                    ) : (
                      <option value="">No bundles available</option>
                    )}
                  </select>
                  <button
                    type="button"
                    className="threads-subtle-btn"
                    onClick={() => applyTopicBundle("replace")}
                    disabled={!selectedTopicBundle?.topics?.length}
                    title="Replace manual labels with this bundle"
                  >
                    Use
                  </button>
                  <button
                    type="button"
                    className="threads-subtle-btn"
                    onClick={() => applyTopicBundle("append")}
                    disabled={!selectedTopicBundle?.topics?.length}
                    title="Append this bundle to current manual labels"
                  >
                    Add
                  </button>
                </div>
                <div className="threads-bundle-row">
                  <input
                    type="text"
                    value={bundleNameInput}
                    onChange={(event) => setBundleNameInput(event.target.value)}
                    placeholder="bundle name (optional)"
                  />
                  <button
                    type="button"
                    className="threads-subtle-btn"
                    onClick={saveManualTopicsAsBundle}
                    disabled={!parseCommaSeparatedTopics(manualThreads).length}
                    title="Save current manual labels as a reusable bundle"
                  >
                    Save bundle
                  </button>
                </div>
                <small className="threads-field-help">
                  Auto bundles are derived from current data. Saved bundles persist on this device.
                </small>
              </label>

              <section className="threads-modal-experimental field-wide">
                <div className="threads-modal-experimental-head">
                  <p className="threads-section-label">Experimental</p>
                  <h4>Sparse autoencoder (SAE)</h4>
                </div>
                <p className="threads-field-help threads-modal-experimental-note">
                  SAE controls are experimental. Today, signal-path settings affect manual-thread
                  label assignment scoring (embeddings + sparse SAE proxy). Automatic clustering
                  remains embeddings-first.
                </p>
                <label
                  className="threads-modal-field"
                  title="Choose the backend used for nugget clustering. scikit-learn stays the stable default."
                >
                  clustering backend
                  <small className="threads-field-help">
                    PyTorch mode is an experimental path to iterate on future large-scale or GPU runs.
                  </small>
                  <select
                    value={clusterBackend}
                    onChange={(event) => setClusterBackend(event.target.value)}
                  >
                    {THREAD_CLUSTER_BACKEND_OPTIONS.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  className="threads-modal-field"
                  title="Preferred device for the experimental clustering backend."
                >
                  clustering device
                  <small className="threads-field-help">
                    Used only by the experimental backend. Auto prefers CUDA when available.
                  </small>
                  <select
                    value={clusterDevice}
                    onChange={(event) => setClusterDevice(event.target.value)}
                    disabled={clusterBackend !== "torch"}
                  >
                    {THREAD_CLUSTER_DEVICE_OPTIONS.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  className="threads-modal-field field-wide"
                  title="Select the scoring signal used when assigning nuggets to manual thread labels."
                >
                  thread signal path
                  <small className="threads-field-help">
                    For now this applies to manual thread-label scoring. In hybrid mode, tune the
                    SAE weight below.
                  </small>
                  <select
                    value={threadSignalMode}
                    onChange={(event) => setThreadSignalMode(event.target.value)}
                  >
                    {THREAD_SIGNAL_MODE_OPTIONS.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  className="threads-modal-field field-wide"
                  title="Hybrid blend factor. 0 = embeddings only, 1 = sparse SAE proxy only."
                >
                  hybrid SAE weight
                  <small className="threads-field-help">
                    Controls the fusion score: combined = (1 - blend)*embedding + blend*SAE_proxy.
                  </small>
                  <div className="threads-slider-row">
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={threadSignalBlend}
                      onChange={(event) => {
                        setThreadSignalBlend(
                          normalizeThreadSignalBlend(event.target.value, 0.7),
                        );
                      }}
                      disabled={threadSignalMode !== "hybrid"}
                    />
                    <span>{Math.round(threadSignalBlend * 100)}% SAE</span>
                  </div>
                </label>
                <label
                  className="threads-modal-field field-wide"
                  title="Choose a pre-approved model+SAE combo. Custom values are allowed for future packs."
                >
                  model + SAE combo
                  <small className="threads-field-help">
                    Preset selector for compatibility tracking; HF downloader integration is a planned follow-up.
                  </small>
                  <input
                    type="text"
                    list="threads-sae-combo-presets"
                    value={saeModelCombo}
                    onChange={(event) => setSaeModelCombo(event.target.value)}
                    placeholder="openai/gpt-oss-20b :: future SAE pack"
                  />
                  <datalist id="threads-sae-combo-presets">
                    {SAE_COMBO_PRESETS.map((preset) => (
                      <option key={preset} value={preset} />
                    ))}
                  </datalist>
                </label>
                <label
                  className="threads-modal-field inline-label"
                  title="When signal path is hybrid/SAE, keep embeddings available as fallback on unsupported runtimes."
                >
                  <input
                    type="checkbox"
                    checked={saeEmbeddingsFallback}
                    onChange={(event) => setSaeEmbeddingsFallback(event.target.checked)}
                  />
                  <span>allow embeddings fallback</span>
                </label>
                <label
                  className="threads-modal-field inline-label"
                  title="Stub toggle for showing live SAE activity in the Agent Console once runtime hooks are available."
                >
                  <input
                    type="checkbox"
                    checked={saeLiveInspectConsole}
                    onChange={(event) => setSaeLiveInspectConsole(event.target.checked)}
                  />
                  <span>live inspect in agent console (stub)</span>
                </label>
                <label className="threads-modal-field inline-label">
                  <input
                    type="checkbox"
                    checked={saeEnabled}
                    onChange={(event) => setSaeEnabled(event.target.checked)}
                  />
                  <span title="Stores SAE inspection/steering parameters with this run for advanced workflows.">
                    enable SAE metadata for this run
                  </span>
                </label>
                {saeEnabled ? (
                  <>
                    <label
                      className="threads-modal-field"
                      title="Inspect logs sparse feature activations; steer stores intervention weights for hook-enabled inference."
                    >
                      SAE mode
                      <small className="threads-field-help">
                        inspect = print active sparse features per token. steer = define feature
                        boosts/suppression for runtime steering hooks.
                      </small>
                      <select value={saeMode} onChange={(event) => setSaeMode(event.target.value)}>
                        <option value="inspect">inspect</option>
                        <option value="steer">steer</option>
                      </select>
                    </label>
                    <label
                      className="threads-modal-field"
                      title="Target transformer layer index for SAE inspection/steering."
                    >
                      SAE layer
                      <small className="threads-field-help">
                        Layer index used to read or modify hidden states.
                      </small>
                      <input
                        type="number"
                        min={0}
                        max={200}
                        value={saeLayer}
                        onChange={(event) => setSaeLayer(event.target.value)}
                      />
                    </label>
                    <label
                      className="threads-modal-field"
                      title="Top-k controls sparsity: number of strongest SAE features retained per token in inspect mode."
                    >
                      SAE top-k
                      <small className="threads-field-help">
                        In inspect mode, each token keeps only the top-k strongest sparse features.
                        Higher k = more detail, lower k = cleaner trace.
                      </small>
                      <input
                        type="number"
                        min={1}
                        max={256}
                        value={saeTopK}
                        onChange={(event) => setSaeTopK(event.target.value)}
                      />
                    </label>
                    <label
                      className="threads-modal-field"
                      title="Token positions to inspect/steer: all, last, or comma-separated indexes."
                    >
                      token positions
                      <small className="threads-field-help">
                        Applies inspection/steering at all tokens, just the last token, or specific indices.
                      </small>
                      <input
                        type="text"
                        value={saeTokenPositions}
                        onChange={(event) => setSaeTokenPositions(event.target.value)}
                        placeholder="all or last"
                      />
                    </label>
                    <label
                      className="threads-modal-field field-wide"
                      title="Per-feature steering weights for steer mode. Positive boosts, negative suppresses."
                    >
                      feature overrides
                      <small className="threads-field-help">
                        Example: <code>123:+0.8,91:-0.4</code>. Each entry is
                        <code>feature_id:alpha</code> for steer mode.
                      </small>
                      <input
                        type="text"
                        value={saeFeatures}
                        onChange={(event) => setSaeFeatures(event.target.value)}
                        placeholder="123:+0.8,91:-0.4"
                      />
                    </label>
                    <label
                      className="threads-modal-field inline-label field-wide"
                      title="Dry-run records intended steering/inspection settings without applying live hidden-state intervention."
                    >
                      <input
                        type="checkbox"
                        checked={saeDryRun}
                        onChange={(event) => setSaeDryRun(event.target.checked)}
                      />
                      <span>dry-run metadata only (no live intervention)</span>
                    </label>
                  </>
                ) : (
                  <p className="threads-field-help threads-modal-experimental-note">
                    SAE controls are currently stored as run metadata and reserved for advanced
                    inspection/steering workflows.
                  </p>
                )}
              </section>
            </div>

            <div className="threads-modal-actions">
              <button type="button" onClick={() => setOptionsOpen(false)}>
                Close
              </button>
              <button type="button" className="threads-btn-primary" onClick={generateFromModal} disabled={loading}>
                {loading ? "Generating..." : "Run generation"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {renameTarget ? (
        <div className="threads-modal-overlay" onClick={closeRename}>
          <div
            className="threads-modal threads-modal-small"
            role="dialog"
            aria-modal="true"
            aria-label="Rename thread"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="threads-modal-header">
              <div>
                <p className="threads-section-label">Rename thread</p>
                <h3>{renameTarget}</h3>
              </div>
              <button type="button" className="threads-modal-close" onClick={closeRename}>
                x
              </button>
            </div>
            <label className="threads-modal-field">
              New label
              <input
                type="text"
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                autoFocus
              />
            </label>
            <div className="threads-modal-actions">
              <button type="button" onClick={closeRename}>
                Cancel
              </button>
              <button type="button" className="threads-btn-primary" onClick={submitRename}>
                Save
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ThreadsTab;


