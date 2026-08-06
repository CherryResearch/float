import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import axios from "axios";
import * as d3 from "d3";

import {
  VISUALIZATION_HEIGHT,
  VISUALIZATION_WIDTH,
  applyManualPlacementForce,
  buildCombinedGraphData,
  buildThreadGraph,
  filterGraphData,
  getBaseNodeRadius,
  getGraphLinkId,
  getLinkEndpointId,
  getNodeFocus,
  hasMeaningfulDrag,
  hydrateKnowledgeGraph,
  hydrateMemoryGraph,
  rankNodeConnections,
  searchGraphNodes,
} from "../utils/knowledgeVisualization";

const GRAPH_PREFERENCES_KEY = "float:knowledge-visualization:v1";
const KNOWLEDGE_NODE_LIMIT = 96;
const KNOWLEDGE_CLAIM_LIMIT = 192;
const EMPTY_GRAPH_TEXT = "";

const GRAPH_OPTIONS = [
  {
    key: "knowledge",
    label: "Knowledge",
    shortDescription: "Stored entities and claims",
  },
  {
    key: "memory",
    label: "Memory",
    shortDescription: "Semantic and provenance relationships",
  },
  {
    key: "threads",
    label: "Threads",
    shortDescription: "Conversation topic projection",
  },
];
const GRAPH_KEYS = new Set(GRAPH_OPTIONS.map((option) => option.key));

const colorByType = new Map([
  ["thread", "var(--graph-node-thread, #56b68b)"],
  ["conversation", "var(--graph-node-conversation, #6f92ff)"],
  ["memory", "var(--graph-node-memory, #f6b66a)"],
  ["person", "var(--graph-node-person, #e58f6f)"],
  ["organization", "var(--graph-node-organization, #9a8cff)"],
  ["place", "var(--graph-node-place, #7bc6d9)"],
  ["event", "var(--graph-node-event, #d8c15f)"],
  ["conversation_anchor", "var(--graph-node-conversation, #8da8ff)"],
  ["file_anchor", "var(--graph-node-file, #8fd3ba)"],
  ["tool_anchor", "var(--graph-node-tool, #f090c5)"],
  ["namespace_anchor", "var(--graph-node-namespace, #d4b1ff)"],
]);

const strokeByLinkType = {
  projection: "var(--graph-edge-projection, rgba(124, 152, 232, 0.34))",
  semantic: "var(--graph-edge-semantic, rgba(245, 181, 108, 0.4))",
  explicit: "var(--graph-edge-explicit, rgba(112, 210, 171, 0.48))",
  claim: "var(--graph-edge-claim, rgba(229, 143, 111, 0.54))",
  cross: "var(--graph-edge-cross, rgba(228, 129, 203, 0.62))",
};

const clampLevel = (value, maxLevel) => {
  const parsed = Number(value || 0);
  const boundedMax = Number.isFinite(maxLevel) ? Math.max(0, Number(maxLevel)) : 0;
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(Math.round(parsed), boundedMax));
};

const readGraphPreferences = () => {
  const defaults = {
    primaryGraphKey: "knowledge",
    comparisonGraphKeys: [],
    levels: { threads: 0, memory: 0, knowledge: 0 },
    planeOffset: 0.2,
    centralNodeIds: {},
  };
  try {
    const parsed = JSON.parse(localStorage.getItem(GRAPH_PREFERENCES_KEY) || "null");
    if (!parsed || typeof parsed !== "object") return defaults;
    const primaryGraphKey = GRAPH_KEYS.has(parsed.primaryGraphKey)
      ? parsed.primaryGraphKey
      : defaults.primaryGraphKey;
    const comparisonGraphKeys = Array.isArray(parsed.comparisonGraphKeys)
      ? parsed.comparisonGraphKeys.filter(
          (key, index, values) =>
            GRAPH_KEYS.has(key) && key !== primaryGraphKey && values.indexOf(key) === index,
        )
      : [];
    return {
      primaryGraphKey,
      comparisonGraphKeys,
      levels: {
        threads: Number(parsed?.levels?.threads || 0),
        memory: Number(parsed?.levels?.memory || 0),
        knowledge: Number(parsed?.levels?.knowledge || 0),
      },
      planeOffset: Number.isFinite(Number(parsed.planeOffset))
        ? Number(parsed.planeOffset)
        : defaults.planeOffset,
      centralNodeIds: Object.fromEntries(
        Object.entries(parsed.centralNodeIds || {}).filter(
          ([graphKey, nodeId]) => GRAPH_KEYS.has(graphKey) && typeof nodeId === "string",
        ),
      ),
    };
  } catch {
    return defaults;
  }
};

const graphRequest = (graphKey, knowledgeLimit) => {
  if (graphKey === "threads") {
    return axios.get("/api/threads/summary").then((response) => ({
      payload: response?.data?.summary || null,
      metadata: {},
    }));
  }
  if (graphKey === "memory") {
    return axios
      .get("/api/memory/graph", {
        params: {
          limit: 72,
          include_thread_projection: false,
          use_loaded_embeddings: false,
        },
      })
      .then((response) => ({
        payload: response?.data?.graph || null,
        metadata: response?.data?.graph?.metadata || {},
      }));
  }
  return axios
    .get("/api/graph", {
      params: {
        limit_nodes: knowledgeLimit,
        limit_claims: Math.min(
          1000,
          Math.max(KNOWLEDGE_CLAIM_LIMIT, knowledgeLimit * 2),
        ),
      },
    })
    .then((response) => ({
      payload: response?.data?.graph || null,
      metadata: response?.data?.graph?.metadata || {},
    }));
};

const graphLabel = (graphKey) =>
  GRAPH_OPTIONS.find((option) => option.key === graphKey)?.label || graphKey;

const graphEmptyMessage = (graphKey) => {
  if (graphKey === "knowledge") {
    return "This knowledge graph is empty. Open Manage graph to add stored nodes and claims.";
  }
  if (graphKey === "memory") {
    return "No memory relationships are available yet. Stored memories and provenance will appear here.";
  }
  return "No thread projection is available yet. Generate or refresh Threads first.";
};

const humanizeKey = (value) => String(value || "").replaceAll("_", " ");

const StructuredValue = ({ value, depth = 0 }) => {
  if (value == null) return <span className="knowledge-viz-empty-value">none</span>;
  if (typeof value === "boolean") return <span>{value ? "true" : "false"}</span>;
  if (typeof value === "number") return <span>{Number.isFinite(value) ? value : String(value)}</span>;
  if (typeof value === "string") {
    if (/^https?:\/\//i.test(value)) {
      return (
        <a href={value} target="_blank" rel="noreferrer">
          {value}
        </a>
      );
    }
    return <span>{value || "empty"}</span>;
  }
  if (depth >= 6) return <code>{JSON.stringify(value)}</code>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="knowledge-viz-empty-value">empty list</span>;
    return (
      <ol className="knowledge-viz-structured-list">
        {value.map((entry, index) => (
          <li key={`${index}:${typeof entry}`}>
            <StructuredValue value={entry} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return <span className="knowledge-viz-empty-value">empty object</span>;
    return (
      <dl className="knowledge-viz-structured-object">
        {entries.map(([key, nestedValue]) => (
          <React.Fragment key={key}>
            <dt>{humanizeKey(key)}</dt>
            <dd>
              <StructuredValue value={nestedValue} depth={depth + 1} />
            </dd>
          </React.Fragment>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
};

const nodeAdditionalData = (node) => {
  const omitted = new Set([
    "id",
    "node_id",
    "label",
    "type",
    "nodeType",
    "node_type",
    "nodeKind",
    "node_kind",
    "graphKey",
    "level",
    "weight",
    "summaryText",
    "summary_text",
    "attributes",
    "createdAt",
    "created_at",
    "updatedAt",
    "updated_at",
    "latestDate",
    "latest_date",
    "matchKey",
    "match_key",
    "anchorX",
    "anchorY",
    "depth",
    "index",
    "x",
    "y",
    "vx",
    "vy",
    "fx",
    "fy",
    "manualX",
    "manualY",
    "__dragMoved",
    "__dragStartX",
    "__dragStartY",
    "isCentral",
  ]);
  return Object.fromEntries(
    Object.entries(node || {}).filter(([key, value]) => !omitted.has(key) && value != null),
  );
};

const linkAdditionalData = (link) => {
  const omitted = new Set([
    "source",
    "target",
    "predicate",
    "category",
    "type",
    "graphKey",
    "weight",
    "confidence",
    "metadata",
    "claim_id",
    "epistemic_status",
    "__graphLinkId",
    "index",
  ]);
  return Object.fromEntries(
    Object.entries(link || {}).filter(([key, value]) => !omitted.has(key) && value != null),
  );
};

const formatDate = (value) => {
  if (value == null || value === "") return "";
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

const linkTouchesNode = (link, nodeId) =>
  Boolean(
    nodeId &&
      (getLinkEndpointId(link?.source) === nodeId ||
        getLinkEndpointId(link?.target) === nodeId),
  );

const applyGraphVisualState = ({
  nodeSelection,
  labelSelection,
  internalLinkSelection,
  crossLinkSelection,
  levels,
  selectedNodeId,
  selectedLinkId,
  zoomScale,
}) => {
  const focusForNode = (node) => getNodeFocus(node, levels, selectedNodeId);
  const allLinks = [...internalLinkSelection.data(), ...crossLinkSelection.data()];
  const selectedLink = allLinks.find((link) => link.__graphLinkId === selectedLinkId);
  const selectedNeighbors = new Set();
  if (selectedNodeId) {
    allLinks.forEach((link) => {
      const sourceId = getLinkEndpointId(link?.source);
      const targetId = getLinkEndpointId(link?.target);
      if (sourceId === selectedNodeId) selectedNeighbors.add(targetId);
      if (targetId === selectedNodeId) selectedNeighbors.add(sourceId);
    });
  }
  const styleLinks = (selection, baseOpacity) =>
    selection
      .attr("opacity", (link) => {
        const isSelectedLink = link.__graphLinkId === selectedLinkId;
        if (selectedLinkId) return isSelectedLink ? 1 : 0.09;
        if (selectedNodeId) return linkTouchesNode(link, selectedNodeId) ? 0.92 : 0.08;
        const sourceFocus = focusForNode(link.source || {});
        const targetFocus = focusForNode(link.target || {});
        return Math.max(0.14, Math.min(sourceFocus.opacity, targetFocus.opacity) * baseOpacity);
      })
      .attr("stroke-width", (link) => {
        const selectedBoost = link.__graphLinkId === selectedLinkId ? 2.2 : 0;
        if (link.type === "projection") return 1.2 + selectedBoost;
        if (link.type === "explicit") return 1.35 + selectedBoost;
        if (link.type === "claim") {
          return 1.45 + Math.min(Number(link.weight || 0), 0.9) + selectedBoost;
        }
        return 1.6 + Math.min(Number(link.weight || 0), 1.4) + selectedBoost;
      });

  styleLinks(internalLinkSelection, 0.92);
  styleLinks(crossLinkSelection, 0.78);

  nodeSelection
    .attr("r", (node) => getBaseNodeRadius(node) * focusForNode(node).scale)
    .attr("opacity", (node) => {
      if (selectedLink && !linkTouchesNode(selectedLink, node.id)) {
        return 0.16;
      }
      if (
        selectedNodeId &&
        node.id !== selectedNodeId &&
        !selectedNeighbors.has(node.id)
      ) {
        return Math.min(0.2, focusForNode(node).opacity);
      }
      return focusForNode(node).opacity;
    })
    .attr("stroke", (node) =>
      node.id === selectedNodeId
        ? "var(--graph-selection, rgba(255, 255, 255, 0.96))"
        : node.isCentral
          ? "var(--graph-central-ring, var(--color-accent))"
          : "var(--graph-node-stroke, rgba(9, 11, 20, 0.7))",
    )
    .attr("stroke-width", (node) => {
      if (node.id === selectedNodeId) return 2.6;
      return node.isCentral ? 2.1 : 0.85;
    });

  labelSelection.attr("opacity", (node) => {
    const focus = focusForNode(node);
    const isSelected = node.id === selectedNodeId;
    const important = node.isCentral || Number(node.weight || 0) >= 3 || node.type === "thread";
    if (zoomScale < 0.52 && !isSelected) return 0;
    if (zoomScale < 0.75 && !important && !isSelected) return 0;
    return focus.opacity >= 0.3 || isSelected ? Math.min(1, focus.opacity + 0.08) : 0;
  });
};

const KnowledgeVisualizationsTab = () => {
  const initialPreferencesRef = useRef(null);
  if (initialPreferencesRef.current === null) {
    initialPreferencesRef.current = readGraphPreferences();
  }
  const initialPreferences = initialPreferencesRef.current;

  const [primaryGraphKey, setPrimaryGraphKey] = useState(
    initialPreferences.primaryGraphKey,
  );
  const [comparisonGraphKeys, setComparisonGraphKeys] = useState(
    initialPreferences.comparisonGraphKeys,
  );
  const [levels, setLevels] = useState(initialPreferences.levels);
  const [planeOffset, setPlaneOffset] = useState(initialPreferences.planeOffset);
  const [centralNodeIds, setCentralNodeIds] = useState(
    initialPreferences.centralNodeIds,
  );
  const [graphPayloads, setGraphPayloads] = useState({
    threads: null,
    memory: null,
    knowledge: null,
  });
  const [loadStates, setLoadStates] = useState({
    threads: { status: "idle", error: "" },
    memory: { status: "idle", error: "" },
    knowledge: { status: "idle", error: "" },
  });
  const [knowledgeLimit, setKnowledgeLimit] = useState(KNOWLEDGE_NODE_LIMIT);
  const [manualGraphText, setManualGraphText] = useState(EMPTY_GRAPH_TEXT);
  const [manualGraphStatus, setManualGraphStatus] = useState("");
  const [manualEditorOpen, setManualEditorOpen] = useState(false);
  const [savingGraph, setSavingGraph] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedLinkId, setSelectedLinkId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [hiddenNodeTypes, setHiddenNodeTypes] = useState([]);
  const [recentDays, setRecentDays] = useState(0);
  const [focusMode, setFocusMode] = useState(false);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [connectionLimit, setConnectionLimit] = useState(12);
  const [manuallyPlacedNodeIds, setManuallyPlacedNodeIds] = useState([]);
  const [layoutRevision, setLayoutRevision] = useState(0);

  const svgRef = useRef(null);
  const mountedRef = useRef(true);
  const loadStatesRef = useRef(loadStates);
  const requestIdsRef = useRef({ threads: 0, memory: 0, knowledge: 0 });
  const zoomBehaviorRef = useRef(null);
  const renderedNodesRef = useRef([]);
  const positionCacheRef = useRef(new Map());
  const manualPositionCacheRef = useRef(new Map());
  const levelsRef = useRef(levels);
  const selectedNodeIdRef = useRef(selectedNodeId);
  const selectedLinkIdRef = useRef(selectedLinkId);
  const zoomScaleRef = useRef(1);
  const cameraDirtyRef = useRef(false);
  const programmaticCameraRef = useRef(false);
  const fittedGraphKeysRef = useRef(new Set());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    levelsRef.current = levels;
  }, [levels]);

  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId;
  }, [selectedNodeId]);

  useEffect(() => {
    selectedLinkIdRef.current = selectedLinkId;
  }, [selectedLinkId]);

  const updateLoadState = useCallback((graphKey, nextState) => {
    const next = {
      ...loadStatesRef.current,
      [graphKey]: nextState,
    };
    loadStatesRef.current = next;
    if (mountedRef.current) setLoadStates(next);
  }, []);

  const loadGraph = useCallback(
    async (graphKey, { force = false, limit = knowledgeLimit } = {}) => {
      const currentStatus = loadStatesRef.current?.[graphKey]?.status;
      if (!force && (currentStatus === "loading" || currentStatus === "ready")) return;
      const requestId = (requestIdsRef.current[graphKey] || 0) + 1;
      requestIdsRef.current[graphKey] = requestId;
      updateLoadState(graphKey, { status: "loading", error: "" });
      try {
        const result = await graphRequest(graphKey, limit);
        if (!mountedRef.current || requestIdsRef.current[graphKey] !== requestId) return;
        setGraphPayloads((current) => ({
          ...current,
          [graphKey]: result.payload,
        }));
        updateLoadState(graphKey, {
          status: "ready",
          error: "",
          metadata: result.metadata,
        });
      } catch {
        if (!mountedRef.current || requestIdsRef.current[graphKey] !== requestId) return;
        updateLoadState(graphKey, {
          status: "error",
          error: `Unable to load the ${graphLabel(graphKey).toLowerCase()} graph.`,
        });
      }
    },
    [knowledgeLimit, updateLoadState],
  );

  const activeGraphKeys = useMemo(
    () => [
      primaryGraphKey,
      ...comparisonGraphKeys.filter((graphKey) => graphKey !== primaryGraphKey),
    ],
    [comparisonGraphKeys, primaryGraphKey],
  );

  useEffect(() => {
    activeGraphKeys.forEach((graphKey) => loadGraph(graphKey));
  }, [activeGraphKeys, loadGraph]);

  useEffect(() => {
    try {
      localStorage.setItem(
        GRAPH_PREFERENCES_KEY,
        JSON.stringify({
          primaryGraphKey,
          comparisonGraphKeys,
          levels,
          planeOffset,
          centralNodeIds,
        }),
      );
    } catch {
      // Persistence is a convenience; graph exploration remains usable without storage.
    }
  }, [centralNodeIds, comparisonGraphKeys, levels, planeOffset, primaryGraphKey]);

  const threadGraph = useMemo(
    () => buildThreadGraph(graphPayloads.threads),
    [graphPayloads.threads],
  );
  const memoryGraph = useMemo(
    () => hydrateMemoryGraph(graphPayloads.memory),
    [graphPayloads.memory],
  );
  const knowledgeGraph = useMemo(
    () => hydrateKnowledgeGraph(graphPayloads.knowledge),
    [graphPayloads.knowledge],
  );

  const maxLevels = useMemo(
    () => ({
      threads: Number(threadGraph?.metadata?.maxLevel || 0),
      memory: Number(memoryGraph?.metadata?.maxLevel || 0),
      knowledge: Number(knowledgeGraph?.metadata?.maxLevel || 0),
    }),
    [knowledgeGraph, memoryGraph, threadGraph],
  );

  useEffect(() => {
    setLevels((current) => ({
      threads: clampLevel(current.threads, maxLevels.threads),
      memory: clampLevel(current.memory, maxLevels.memory),
      knowledge: clampLevel(current.knowledge, maxLevels.knowledge),
    }));
  }, [maxLevels.knowledge, maxLevels.memory, maxLevels.threads]);

  const graphData = useMemo(
    () => {
      const combined = buildCombinedGraphData({
        threadGraph,
        memoryGraph,
        knowledgeGraph,
        includeThreadProjection: activeGraphKeys.includes("threads"),
        includeMemoryProjection: activeGraphKeys.includes("memory"),
        includeKnowledgeOverlay: activeGraphKeys.includes("knowledge"),
        planeOffset,
      });
      return {
        ...combined,
        nodes: combined.nodes.map((node) => ({
          ...node,
          isCentral: centralNodeIds[node.graphKey] === node.id,
        })),
      };
    },
    [
      activeGraphKeys,
      centralNodeIds,
      knowledgeGraph,
      memoryGraph,
      planeOffset,
      threadGraph,
    ],
  );

  const nodeTypes = useMemo(
    () =>
      Array.from(new Set(graphData.nodes.map((node) => String(node.type || "unknown")))).sort(),
    [graphData.nodes],
  );
  const focusNodeIdForFilter = focusMode ? selectedNodeId : "";
  const visibleGraphData = useMemo(
    () =>
      filterGraphData(graphData, {
        excludedNodeTypes: hiddenNodeTypes,
        recentDays,
        focusNodeId: focusNodeIdForFilter,
        focusMode,
      }),
    [focusMode, focusNodeIdForFilter, graphData, hiddenNodeTypes, recentDays],
  );
  const searchResults = useMemo(
    () => searchGraphNodes(visibleGraphData.nodes, searchQuery, 8),
    [searchQuery, visibleGraphData.nodes],
  );
  const selectedNode = useMemo(
    () => graphData.nodes.find((node) => node.id === selectedNodeId) || null,
    [graphData.nodes, selectedNodeId],
  );
  const allGraphLinks = useMemo(
    () => [...graphData.links, ...graphData.crossLinks],
    [graphData.crossLinks, graphData.links],
  );
  const selectedLink = useMemo(
    () =>
      allGraphLinks.find(
        (link, index) => (link.__graphLinkId || getGraphLinkId(link, index)) === selectedLinkId,
      ) || null,
    [allGraphLinks, selectedLinkId],
  );
  const selectedConnections = useMemo(
    () => rankNodeConnections(selectedNodeId, graphData.nodes, allGraphLinks),
    [allGraphLinks, graphData.nodes, selectedNodeId],
  );

  useEffect(() => {
    if (!selectedNodeId) return;
    if (!graphData.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId("");
      setFocusMode(false);
    }
  }, [graphData.nodes, selectedNodeId]);

  useEffect(() => {
    setConnectionLimit(12);
  }, [selectedNodeId]);

  const applyFit = useCallback(
    (nodes = renderedNodesRef.current) => {
      const svgElement = svgRef.current;
      const zoomBehavior = zoomBehaviorRef.current;
      const positionedNodes = (nodes || []).filter(
        (node) => Number.isFinite(node?.x) && Number.isFinite(node?.y),
      );
      if (!svgElement || !zoomBehavior || !positionedNodes.length) return;
      const minX = Math.min(...positionedNodes.map((node) => node.x));
      const maxX = Math.max(...positionedNodes.map((node) => node.x));
      const minY = Math.min(...positionedNodes.map((node) => node.y));
      const maxY = Math.max(...positionedNodes.map((node) => node.y));
      const centralNode = positionedNodes.find(
        (node) => node.isCentral && node.graphKey === primaryGraphKey,
      );
      const centerX = centralNode?.x ?? (minX + maxX) / 2;
      const centerY = centralNode?.y ?? (minY + maxY) / 2;
      const contentWidth = Math.max(
        80,
        centralNode
          ? Math.max(Math.abs(maxX - centerX), Math.abs(centerX - minX)) * 2
          : maxX - minX,
      );
      const contentHeight = Math.max(
        80,
        centralNode
          ? Math.max(Math.abs(maxY - centerY), Math.abs(centerY - minY)) * 2
          : maxY - minY,
      );
      const scale = Math.max(
        0.25,
        Math.min(
          2.5,
          (VISUALIZATION_WIDTH - 150) / contentWidth,
          (VISUALIZATION_HEIGHT - 130) / contentHeight,
        ),
      );
      const transform = d3.zoomIdentity
        .translate(VISUALIZATION_WIDTH / 2, VISUALIZATION_HEIGHT / 2)
        .scale(scale)
        .translate(-centerX, -centerY);
      programmaticCameraRef.current = true;
      d3.select(svgElement).call(zoomBehavior.transform, transform);
      programmaticCameraRef.current = false;
    },
    [primaryGraphKey],
  );

  const centerNode = useCallback((nodeId) => {
    const svgElement = svgRef.current;
    const zoomBehavior = zoomBehaviorRef.current;
    const node = renderedNodesRef.current.find((candidate) => candidate.id === nodeId);
    if (!svgElement || !zoomBehavior || !Number.isFinite(node?.x) || !Number.isFinite(node?.y)) {
      return;
    }
    const scale = Math.max(1.15, zoomScaleRef.current);
    const transform = d3.zoomIdentity
      .translate(VISUALIZATION_WIDTH / 2, VISUALIZATION_HEIGHT / 2)
      .scale(scale)
      .translate(-node.x, -node.y);
    programmaticCameraRef.current = true;
    d3.select(svgElement).call(zoomBehavior.transform, transform);
    programmaticCameraRef.current = false;
  }, []);

  const zoomBy = useCallback((factor) => {
    if (!svgRef.current || !zoomBehaviorRef.current) return;
    programmaticCameraRef.current = true;
    d3.select(svgRef.current).call(zoomBehaviorRef.current.scaleBy, factor);
    programmaticCameraRef.current = false;
  }, []);

  const resetCamera = useCallback(() => {
    if (!svgRef.current || !zoomBehaviorRef.current) return;
    programmaticCameraRef.current = true;
    d3.select(svgRef.current).call(zoomBehaviorRef.current.transform, d3.zoomIdentity);
    programmaticCameraRef.current = false;
  }, []);

  useEffect(() => {
    const svgElement = svgRef.current;
    if (!svgElement) return undefined;

    const svg = d3.select(svgElement);
    const positionCache = positionCacheRef.current;
    const manualPositions = manualPositionCacheRef.current;
    svg.selectAll("*").remove();
    svg.attr("viewBox", [0, 0, VISUALIZATION_WIDTH, VISUALIZATION_HEIGHT]);

    const { nodes, links, crossLinks } = visibleGraphData;
    renderedNodesRef.current = nodes;
    if (!nodes.length) return undefined;

    nodes.forEach((node) => {
      const cached = positionCache.get(node.id);
      const manual = manualPositions.get(node.id);
      if (manual) {
        node.x = manual.x;
        node.y = manual.y;
        node.manualX = manual.x;
        node.manualY = manual.y;
      } else if (node.isCentral) {
        node.x = VISUALIZATION_WIDTH / 2;
        node.y = VISUALIZATION_HEIGHT / 2;
      } else if (cached) {
        node.x = cached.x;
        node.y = cached.y;
      } else {
        node.x = Number(node.anchorX || VISUALIZATION_WIDTH / 2);
        node.y = Number(node.anchorY || VISUALIZATION_HEIGHT / 2);
      }
    });

    const viewport = svg.append("g").attr("class", "knowledge-viz-viewport");
    const zoomBehavior = d3
      .zoom()
      .scaleExtent([0.2, 5])
      .filter((event) => !event.button && event.type !== "dblclick")
      .on("zoom", (event) => {
        viewport.attr("transform", event.transform);
        zoomScaleRef.current = Number(event.transform?.k || 1);
        setZoomPercent(Math.round(zoomScaleRef.current * 100));
        if (!programmaticCameraRef.current) cameraDirtyRef.current = true;
        const labels = viewport.selectAll(".knowledge-viz-labels text");
        labels.attr("opacity", (node) => {
          if (
            zoomScaleRef.current < 0.52 &&
            !node.isCentral &&
            node.id !== selectedNodeIdRef.current
          ) {
            return 0;
          }
          if (
            zoomScaleRef.current < 0.75 &&
            !node.isCentral &&
            Number(node.weight || 0) < 3 &&
            node.type !== "thread" &&
            node.id !== selectedNodeIdRef.current
          ) {
            return 0;
          }
          return getNodeFocus(node, levelsRef.current, selectedNodeIdRef.current).opacity;
        });
      });
    zoomBehaviorRef.current = zoomBehavior;
    svg.call(zoomBehavior).on("dblclick.zoom", null);

    const preparedLinks = links.map((link, index) => ({
      ...link,
      __graphLinkId: getGraphLinkId(link, index),
    }));
    const preparedCrossLinks = crossLinks.map((link, index) => ({
      ...link,
      __graphLinkId: getGraphLinkId(link, preparedLinks.length + index),
    }));
    const nodeLookup = new Map(nodes.map((node) => [node.id, node]));

    const internalLinkSelection = viewport
      .append("g")
      .attr("class", "knowledge-viz-links")
      .selectAll("line")
      .data(preparedLinks, (link) => link.__graphLinkId)
      .join("line")
      .attr("stroke", (link) =>
        strokeByLinkType[link.type] || "rgba(138, 154, 199, 0.42)",
      )
      .attr("stroke-dasharray", (link) => (link.type === "explicit" ? "4 5" : null))
      .attr("stroke-linecap", "round")
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (link) => `Relationship ${link.predicate || link.type}`)
      .on("click", (event, link) => {
        event.stopPropagation();
        setSelectedLinkId(link.__graphLinkId);
        setSelectedNodeId("");
      })
      .on("keydown", (event, link) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        setSelectedLinkId(link.__graphLinkId);
        setSelectedNodeId("");
      });

    const crossLinkSelection = viewport
      .append("g")
      .attr("class", "knowledge-viz-cross-links")
      .selectAll("line")
      .data(preparedCrossLinks, (link) => link.__graphLinkId)
      .join("line")
      .attr("stroke", strokeByLinkType.cross)
      .attr("stroke-dasharray", "2 6")
      .attr("stroke-linecap", "round")
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", "Cross-graph bridge")
      .on("click", (event, link) => {
        event.stopPropagation();
        setSelectedLinkId(link.__graphLinkId);
        setSelectedNodeId("");
      });

    const nodeSelection = viewport
      .append("g")
      .attr("class", "knowledge-viz-nodes")
      .selectAll("circle")
      .data(nodes, (node) => node.id)
      .join("circle")
      .attr("fill", (node) => colorByType.get(node.type) || "#9ab0d9")
      .attr("data-central", (node) => (node.isCentral ? "true" : null))
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (node) => `${node.label}, ${node.type}`)
      .on("click", (event, node) => {
        event.stopPropagation();
        setSelectedNodeId(node.id);
        setSelectedLinkId("");
      })
      .on("dblclick", (event, node) => {
        event.stopPropagation();
        setSelectedNodeId(node.id);
        setSelectedLinkId("");
        centerNode(node.id);
      })
      .on("keydown", (event, node) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        setSelectedNodeId(node.id);
        setSelectedLinkId("");
      });
    nodeSelection
      .append("title")
      .text(
        (node) =>
          `${node.label} · ${node.type}${node.isCentral ? " · central node" : ""}`,
      );

    const labelSelection = viewport
      .append("g")
      .attr("class", "knowledge-viz-labels")
      .attr("fill", "currentColor")
      .attr("pointer-events", "none")
      .selectAll("text")
      .data(nodes, (node) => node.id)
      .join("text")
      .attr("text-anchor", "middle")
      .attr("font-size", (node) => {
        if (node.isCentral) return 13;
        if (node.graphKey === "knowledge") return 12;
        return node.type === "thread" ? 11 : 10;
      })
      .attr("dy", (node) => {
        const offset = node.isCentral
          ? getBaseNodeRadius(node) + 9
          : node.graphKey === "knowledge"
            ? 19
            : 14;
        return node.level === 0 ? -offset : offset;
      })
      .text((node) => {
        const label = String(node.label || "");
        return label.length > 30 ? `${label.slice(0, 28)}…` : label;
      });

    let frameId = null;
    const renderPositions = () => {
      frameId = null;
      internalLinkSelection
        .attr("x1", (link) => link.source.x)
        .attr("y1", (link) => link.source.y)
        .attr("x2", (link) => link.target.x)
        .attr("y2", (link) => link.target.y);
      crossLinkSelection
        .attr("x1", (link) => nodeLookup.get(getLinkEndpointId(link.source))?.x || 0)
        .attr("y1", (link) => nodeLookup.get(getLinkEndpointId(link.source))?.y || 0)
        .attr("x2", (link) => nodeLookup.get(getLinkEndpointId(link.target))?.x || 0)
        .attr("y2", (link) => nodeLookup.get(getLinkEndpointId(link.target))?.y || 0);
      nodeSelection.attr("cx", (node) => node.x).attr("cy", (node) => node.y);
      labelSelection.attr("x", (node) => node.x).attr("y", (node) => node.y);
      applyGraphVisualState({
        nodeSelection,
        labelSelection,
        internalLinkSelection,
        crossLinkSelection,
        levels: levelsRef.current,
        selectedNodeId: selectedNodeIdRef.current,
        selectedLinkId: selectedLinkIdRef.current,
        zoomScale: zoomScaleRef.current,
      });
    };
    const queueRender = () => {
      if (frameId != null) return;
      frameId = window.requestAnimationFrame(renderPositions);
    };

    const simulation = d3
      .forceSimulation(nodes)
      .alphaDecay(0.055)
      .alphaMin(0.025)
      .velocityDecay(0.42)
      .force(
        "link",
        d3
          .forceLink(preparedLinks)
          .id((node) => node.id)
          .distance((link) => {
            if (link.type === "projection") return 120;
            if (link.type === "explicit") return 88;
            if (link.type === "claim") return 190;
            const weight = Number(link.weight || 0);
            return Math.max(42, 128 - Math.min(weight * 90, 74));
          })
          .strength((link) => (link.type === "semantic" ? 0.28 : 0.45)),
      )
      .force(
        "charge",
        d3.forceManyBody().strength((node) => {
          if (node.type === "thread") return -200;
          if (node.type === "memory") return -160;
          if (node.graphKey === "knowledge") return -340;
          return -95;
        }),
      )
      .force(
        "x",
        d3
          .forceX((node) =>
            node.isCentral && !manualPositions.has(node.id)
              ? VISUALIZATION_WIDTH / 2
              : Number(node.anchorX || VISUALIZATION_WIDTH / 2),
          )
          .strength((node) => {
            if (node.isCentral && !manualPositions.has(node.id)) return 0.62;
            if (node.graphKey === "knowledge") return 0.36;
            return visibleGraphData.metadata.activeGraphCount > 1 ? 0.22 : 0.12;
          }),
      )
      .force(
        "y",
        d3
          .forceY(
            (node) => {
              if (node.isCentral && !manualPositions.has(node.id)) {
                return VISUALIZATION_HEIGHT / 2;
              }
              return (
                Number(node.anchorY || VISUALIZATION_HEIGHT / 2) +
                (Number(node.level || 0) -
                  Number(levelsRef.current?.[node.graphKey] || 0)) *
                  34
              );
            },
          )
          .strength((node) => {
            if (node.isCentral && !manualPositions.has(node.id)) return 0.62;
            return node.graphKey === "knowledge" ? 0.34 : 0.18;
          }),
      )
      .force("manual-placement", (alpha) =>
        applyManualPlacementForce(nodes, manualPositions, alpha),
      )
      .force(
        "collision",
        d3.forceCollide().radius((node) => getBaseNodeRadius(node) + 11),
      )
      .on("tick", queueRender)
      .on("end", () => {
        renderPositions();
        nodes.forEach((node) => {
          positionCache.set(node.id, { x: node.x, y: node.y });
        });
        const graphSignature = `${activeGraphKeys.join("+")}:${nodes.length}`;
        if (!cameraDirtyRef.current && !fittedGraphKeysRef.current.has(graphSignature)) {
          fittedGraphKeysRef.current.add(graphSignature);
          applyFit(nodes);
        }
      });

    const drag = d3
      .drag()
      .on("start", (event, node) => {
        event.sourceEvent?.stopPropagation();
        if (!event.active) simulation.alphaTarget(0.2).restart();
        node.__dragMoved = false;
        node.__dragStartX = node.x;
        node.__dragStartY = node.y;
        node.fx = node.x;
        node.fy = node.y;
      })
      .on("drag", (event, node) => {
        if (
          hasMeaningfulDrag(
            { x: node.__dragStartX, y: node.__dragStartY },
            { x: event.x, y: event.y },
          )
        ) {
          node.__dragMoved = true;
        }
        node.fx = event.x;
        node.fy = event.y;
      })
      .on("end", (event, node) => {
        if (!event.active) simulation.alphaTarget(0);
        const x = Number.isFinite(node.fx) ? node.fx : node.x;
        const y = Number.isFinite(node.fy) ? node.fy : node.y;
        node.x = x;
        node.y = y;
        if (node.__dragMoved) {
          node.manualX = x;
          node.manualY = y;
          manualPositions.set(node.id, { x, y });
          positionCache.set(node.id, { x, y });
          setManuallyPlacedNodeIds(Array.from(manualPositions.keys()));
        }
        node.fx = null;
        node.fy = null;
        delete node.__dragMoved;
        delete node.__dragStartX;
        delete node.__dragStartY;
      });
    nodeSelection.call(drag);

    svg.on("click", () => {
      setSelectedNodeId("");
      setSelectedLinkId("");
      setFocusMode(false);
    });
    renderPositions();

    return () => {
      simulation.stop();
      if (frameId != null) window.cancelAnimationFrame(frameId);
      nodes.forEach((node) => {
        if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
          positionCache.set(node.id, { x: node.x, y: node.y });
        }
      });
      svg.on(".zoom", null).on("click", null);
    };
  }, [activeGraphKeys, applyFit, centerNode, layoutRevision, visibleGraphData]);

  useEffect(() => {
    const svgElement = svgRef.current;
    if (!svgElement || !visibleGraphData.nodes.length) return;
    const svg = d3.select(svgElement);
    applyGraphVisualState({
      nodeSelection: svg.selectAll(".knowledge-viz-nodes circle"),
      labelSelection: svg.selectAll(".knowledge-viz-labels text"),
      internalLinkSelection: svg.selectAll(".knowledge-viz-links line"),
      crossLinkSelection: svg.selectAll(".knowledge-viz-cross-links line"),
      levels,
      selectedNodeId,
      selectedLinkId,
      zoomScale: zoomScaleRef.current,
    });
  }, [levels, selectedLinkId, selectedNodeId, visibleGraphData.nodes.length]);

  const changePrimaryGraph = (graphKey) => {
    setPrimaryGraphKey(graphKey);
    setComparisonGraphKeys((current) => current.filter((key) => key !== graphKey));
    setSelectedNodeId("");
    setSelectedLinkId("");
    setFocusMode(false);
    setSearchQuery("");
    setHiddenNodeTypes([]);
    setRecentDays(0);
    cameraDirtyRef.current = false;
  };

  const toggleComparisonGraph = (graphKey) => {
    if (graphKey === primaryGraphKey) return;
    setComparisonGraphKeys((current) =>
      current.includes(graphKey)
        ? current.filter((key) => key !== graphKey)
        : [...current, graphKey],
    );
    cameraDirtyRef.current = false;
  };

  const changeLevel = (graphKey, next) => {
    setLevels((current) => ({
      ...current,
      [graphKey]: clampLevel((current?.[graphKey] || 0) + next, maxLevels?.[graphKey]),
    }));
  };

  const toggleNodeType = (nodeType) => {
    setHiddenNodeTypes((current) =>
      current.includes(nodeType)
        ? current.filter((value) => value !== nodeType)
        : [...current, nodeType],
    );
    cameraDirtyRef.current = false;
  };

  const selectSearchResult = (node) => {
    setSelectedNodeId(node.id);
    setSelectedLinkId("");
    setSearchQuery("");
    window.requestAnimationFrame(() => centerNode(node.id));
  };

  const selectInspectorNode = (nodeId) => {
    setSelectedNodeId(nodeId);
    setSelectedLinkId("");
    window.requestAnimationFrame(() => centerNode(nodeId));
  };

  const selectInspectorLink = (linkId) => {
    setSelectedLinkId(linkId);
    setSelectedNodeId("");
  };

  const toggleCentralNode = () => {
    if (!selectedNode) return;
    const graphKey = selectedNode.graphKey;
    const isCentral = centralNodeIds[graphKey] === selectedNode.id;
    if (!isCentral) {
      manualPositionCacheRef.current.delete(selectedNode.id);
      positionCacheRef.current.delete(selectedNode.id);
      setManuallyPlacedNodeIds(Array.from(manualPositionCacheRef.current.keys()));
    }
    setCentralNodeIds((current) => ({
      ...current,
      [graphKey]: isCentral ? "" : selectedNode.id,
    }));
    setLayoutRevision((current) => current + 1);
    fittedGraphKeysRef.current.clear();
    cameraDirtyRef.current = false;
  };

  const releaseSelectedNodePlacement = () => {
    if (!selectedNode) return;
    manualPositionCacheRef.current.delete(selectedNode.id);
    positionCacheRef.current.delete(selectedNode.id);
    setManuallyPlacedNodeIds(Array.from(manualPositionCacheRef.current.keys()));
    setLayoutRevision((current) => current + 1);
    fittedGraphKeysRef.current.clear();
    cameraDirtyRef.current = false;
  };

  const applyManualGraphUpdate = async () => {
    setManualGraphStatus("");
    if (!manualGraphText.trim()) {
      setManualEditorOpen(true);
      setManualGraphStatus("Paste graph JSON before applying.");
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(manualGraphText);
    } catch {
      setManualGraphStatus("Invalid graph JSON.");
      return;
    }
    const body = {
      nodes: Array.isArray(parsed?.nodes)
        ? parsed.nodes
        : Array.isArray(parsed?.graph_nodes)
          ? parsed.graph_nodes
          : [],
      claims: Array.isArray(parsed?.claims)
        ? parsed.claims
        : Array.isArray(parsed?.graph_claims)
          ? parsed.graph_claims
          : [],
      source_kind: parsed?.source_kind || "manual",
      source_ref: parsed?.source_ref || "manual_graph_entry",
    };
    if (!body.nodes.length && !body.claims.length) {
      setManualGraphStatus("Graph JSON needs nodes or claims.");
      return;
    }
    setSavingGraph(true);
    try {
      const response = await axios.post("/api/graph", body);
      const graph = response?.data?.graph || null;
      const update = response?.data?.graph_update || {};
      const revision = response?.data?.revision;
      setGraphPayloads((current) => ({ ...current, knowledge: graph }));
      updateLoadState("knowledge", {
        status: "ready",
        error: "",
        metadata: graph?.metadata || {},
      });
      changePrimaryGraph("knowledge");
      setManualEditorOpen(false);
      setManualGraphStatus(
        [
          `Applied ${update.node_count || 0} nodes and ${update.claim_count || 0} claims`,
          revision?.action_id ? `revision ${String(revision.action_id).slice(-8)}` : "",
        ]
          .filter(Boolean)
          .join("; ") + ".",
      );
    } catch {
      setManualGraphStatus("Unable to apply graph update.");
    } finally {
      setSavingGraph(false);
    }
  };

  const loadMoreKnowledge = () => {
    const nextLimit = Math.min(500, Math.max(knowledgeLimit * 2, knowledgeLimit + 48));
    setKnowledgeLimit(nextLimit);
    loadGraph("knowledge", { force: true, limit: nextLimit });
  };

  const activeLoading = activeGraphKeys.some(
    (graphKey) => loadStates[graphKey]?.status === "loading",
  );
  const activeErrors = activeGraphKeys
    .map((graphKey) => loadStates[graphKey]?.error)
    .filter(Boolean);
  const activeGraphCount = graphData.metadata.activeGraphCount;
  const activeLinkTypes = Array.from(
    new Set([...visibleGraphData.links, ...visibleGraphData.crossLinks].map((link) => link.type)),
  );
  const knowledgeMetadata = knowledgeGraph?.metadata || {};
  const totalKnowledgeNodes = Number(
    knowledgeMetadata.total_node_count || knowledgeMetadata.node_count || 0,
  );
  const totalKnowledgeClaims = Number(
    knowledgeMetadata.total_claim_count || knowledgeMetadata.claim_count || 0,
  );
  const knowledgeTruncated = Boolean(
    knowledgeMetadata.truncated ||
      knowledgeMetadata.nodes_truncated ||
      totalKnowledgeNodes > Number(knowledgeMetadata.node_count || 0),
  );
  const selectedLinkSource = selectedLink
    ? graphData.nodes.find((node) => node.id === getLinkEndpointId(selectedLink.source))
    : null;
  const selectedLinkTarget = selectedLink
    ? graphData.nodes.find((node) => node.id === getLinkEndpointId(selectedLink.target))
    : null;
  const selectedNodeIsCentral = Boolean(
    selectedNode && centralNodeIds[selectedNode.graphKey] === selectedNode.id,
  );
  const selectedNodeIsManuallyPlaced = Boolean(
    selectedNode && manuallyPlacedNodeIds.includes(selectedNode.id),
  );
  const selectedNodeAdditionalData = nodeAdditionalData(selectedNode);
  const selectedLinkAdditionalData = linkAdditionalData(selectedLink);

  return (
    <section className="knowledge-viz-tab">
      <header className="knowledge-viz-head">
        <div>
          <h3>Graph explorer</h3>
          <p className="status-note">
            Explore stored knowledge, memory provenance, and thread projections in one
            focused workspace.
          </p>
        </div>
        <div className="knowledge-viz-primary-picker" role="radiogroup" aria-label="Graph">
          {GRAPH_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              role="radio"
              aria-checked={primaryGraphKey === option.key}
              className={primaryGraphKey === option.key ? "active" : ""}
              data-graph-key={option.key}
              onClick={() => changePrimaryGraph(option.key)}
              title={option.shortDescription}
            >
              <span className="knowledge-viz-graph-dot" aria-hidden="true" />
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      </header>

      <div className="knowledge-viz-toolbar">
        <div className="knowledge-viz-search">
          <label htmlFor="knowledge-viz-search">Search nodes</label>
          <input
            id="knowledge-viz-search"
            type="search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Find a person, place, topic…"
            autoComplete="off"
          />
          {searchQuery ? (
            <div className="knowledge-viz-search-results" role="listbox">
              {searchResults.length ? (
                searchResults.map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    role="option"
                    aria-selected={node.id === selectedNodeId}
                    onClick={() => selectSearchResult(node)}
                  >
                    <span>{node.label}</span>
                    <small>
                      {graphLabel(node.graphKey)} · {node.type}
                    </small>
                  </button>
                ))
              ) : (
                <p>No matching nodes in the current view.</p>
              )}
            </div>
          ) : null}
        </div>

        <details className="knowledge-viz-popover knowledge-viz-popover--secondary">
          <summary>
            Filters
            {hiddenNodeTypes.length || recentDays ? (
              <span>{hiddenNodeTypes.length + (recentDays ? 1 : 0)}</span>
            ) : null}
          </summary>
          <div className="knowledge-viz-popover-panel">
            <fieldset>
              <legend>Node types</legend>
              {nodeTypes.length ? (
                nodeTypes.map((nodeType) => (
                  <label key={nodeType}>
                    <input
                      type="checkbox"
                      checked={!hiddenNodeTypes.includes(nodeType)}
                      onChange={() => toggleNodeType(nodeType)}
                    />
                    {nodeType.replaceAll("_", " ")}
                  </label>
                ))
              ) : (
                <span className="status-note">No node types loaded.</span>
              )}
              {hiddenNodeTypes.length ? (
                <button type="button" onClick={() => setHiddenNodeTypes([])}>
                  Show all types
                </button>
              ) : null}
            </fieldset>
            <label className="knowledge-viz-recency-filter">
              Recently updated
              <select
                value={recentDays}
                onChange={(event) => {
                  setRecentDays(Number(event.target.value));
                  cameraDirtyRef.current = false;
                }}
              >
                <option value="0">Any time</option>
                <option value="7">Last 7 days</option>
                <option value="30">Last 30 days</option>
                <option value="90">Last 90 days</option>
              </select>
            </label>
          </div>
        </details>

        <details className="knowledge-viz-popover knowledge-viz-popover--primary">
          <summary>
            Layers
            {comparisonGraphKeys.length ? <span>{comparisonGraphKeys.length}</span> : null}
          </summary>
          <div className="knowledge-viz-popover-panel">
            <p className="status-note">Add a projection for comparison.</p>
            {GRAPH_OPTIONS.filter((option) => option.key !== primaryGraphKey).map((option) => (
              <label key={option.key}>
                <input
                  type="checkbox"
                  checked={comparisonGraphKeys.includes(option.key)}
                  onChange={() => toggleComparisonGraph(option.key)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </details>

        <details className="knowledge-viz-popover knowledge-viz-popover--secondary knowledge-viz-manage">
          <summary>Manage graph</summary>
          <div className="knowledge-viz-popover-panel knowledge-viz-manage-panel">
            <strong>Manual graph entry</strong>
            <p className="status-note">
              Advanced: apply stored graph nodes and claims from JSON.
            </p>
            <button type="button" onClick={() => setManualEditorOpen((current) => !current)}>
              {manualEditorOpen ? "Hide JSON" : "Paste JSON"}
            </button>
            {manualEditorOpen ? (
              <textarea
                aria-label="Graph update JSON"
                value={manualGraphText}
                onChange={(event) => setManualGraphText(event.target.value)}
                spellCheck={false}
              />
            ) : null}
            <button
              type="button"
              className="primary"
              onClick={applyManualGraphUpdate}
              disabled={savingGraph}
            >
              {savingGraph ? "Applying…" : "Apply graph update"}
            </button>
            {manualGraphStatus ? <p className="status-note">{manualGraphStatus}</p> : null}
          </div>
        </details>
      </div>

      {activeGraphCount > 1 ? (
        <div className="knowledge-viz-layer-settings">
          {activeGraphKeys.map((graphKey) => (
            <div className="knowledge-viz-level-control" key={graphKey}>
              <strong>{graphLabel(graphKey)}</strong>
              <button
                type="button"
                aria-label={`Decrease ${graphKey} level`}
                onClick={() => changeLevel(graphKey, -1)}
                disabled={levels[graphKey] <= 0}
              >
                ↓
              </button>
              <span>
                level {levels[graphKey] + 1}/{maxLevels[graphKey] + 1}
              </span>
              <button
                type="button"
                aria-label={`Increase ${graphKey} level`}
                onClick={() => changeLevel(graphKey, 1)}
                disabled={levels[graphKey] >= maxLevels[graphKey]}
              >
                ↑
              </button>
            </div>
          ))}
          <label className="knowledge-viz-plane" htmlFor="knowledge-viz-plane-offset">
            layer spacing
            <input
              id="knowledge-viz-plane-offset"
              type="range"
              min="-1"
              max="1"
              step="0.05"
              value={planeOffset}
              onChange={(event) => {
                setPlaneOffset(Number(event.target.value));
                cameraDirtyRef.current = false;
              }}
            />
            <span>{planeOffset.toFixed(2)}</span>
          </label>
        </div>
      ) : maxLevels[primaryGraphKey] > 0 ? (
        <div className="knowledge-viz-layer-settings compact">
          <div className="knowledge-viz-level-control">
            <strong>{graphLabel(primaryGraphKey)}</strong>
            <button
              type="button"
              aria-label={`Decrease ${primaryGraphKey} level`}
              onClick={() => changeLevel(primaryGraphKey, -1)}
              disabled={levels[primaryGraphKey] <= 0}
            >
              ↓
            </button>
            <span>
              level {levels[primaryGraphKey] + 1}/{maxLevels[primaryGraphKey] + 1}
            </span>
            <button
              type="button"
              aria-label={`Increase ${primaryGraphKey} level`}
              onClick={() => changeLevel(primaryGraphKey, 1)}
              disabled={levels[primaryGraphKey] >= maxLevels[primaryGraphKey]}
            >
              ↑
            </button>
          </div>
        </div>
      ) : null}

      <div className="knowledge-viz-layout">
        <div className="knowledge-viz-canvas-shell">
          <div className="knowledge-viz-canvas-toolbar" aria-label="Graph view controls">
            <button type="button" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
              +
            </button>
            <button type="button" onClick={() => zoomBy(0.8)} aria-label="Zoom out">
              −
            </button>
            <button type="button" onClick={() => applyFit()}>
              Fit
            </button>
            <button type="button" onClick={resetCamera}>
              Reset
            </button>
            <span aria-live="polite">{zoomPercent}%</span>
          </div>

          {activeLoading && !visibleGraphData.nodes.length ? (
            <div className="knowledge-viz-canvas-state" role="status">
              <span className="knowledge-viz-loading-orbit" aria-hidden="true" />
              Loading {activeGraphKeys.map(graphLabel).join(" + ")}…
            </div>
          ) : null}
          {!activeLoading && !activeErrors.length && !visibleGraphData.nodes.length ? (
            <div className="knowledge-viz-canvas-state">
              {graphData.nodes.length
                ? "No nodes match the current filters."
                : graphEmptyMessage(primaryGraphKey)}
            </div>
          ) : null}
          {activeErrors.length && !visibleGraphData.nodes.length ? (
            <div className="knowledge-viz-canvas-state warn" role="alert">
              <span>{activeErrors.join(" ")}</span>
              <button
                type="button"
                onClick={() =>
                  activeGraphKeys.forEach((graphKey) => loadGraph(graphKey, { force: true }))
                }
              >
                Try again
              </button>
            </div>
          ) : null}

          <svg
            ref={svgRef}
            className="knowledge-viz-canvas"
            aria-label={`${activeGraphKeys.map(graphLabel).join(" and ")} graph`}
          />

          <div className="knowledge-viz-canvas-footer">
            <span>
              {visibleGraphData.nodes.length}
              {visibleGraphData.metadata.unfilteredNodeCount !== visibleGraphData.nodes.length
                ? ` of ${visibleGraphData.metadata.unfilteredNodeCount}`
                : ""}{" "}
              nodes
            </span>
            <span>
              {visibleGraphData.links.length + visibleGraphData.crossLinks.length} relationships
            </span>
            {activeLoading && visibleGraphData.nodes.length ? <span>Refreshing…</span> : null}
            {focusMode ? <span>One-hop focus</span> : null}
            {knowledgeTruncated && activeGraphKeys.includes("knowledge") ? (
              <button type="button" onClick={loadMoreKnowledge} disabled={knowledgeLimit >= 500}>
                Showing {knowledgeGraph.nodes.length} of {totalKnowledgeNodes}; load more
              </button>
            ) : null}
          </div>
        </div>

        <aside
          className={`knowledge-viz-side${selectedNode || selectedLink ? " has-selection" : ""}`}
        >
          <div className="knowledge-viz-side-head">
            <h4>
              {selectedNode ? "Node" : selectedLink ? "Relationship" : "Inspector"}
            </h4>
            {selectedNode ? (
              <span>
                {selectedConnections.length} link{selectedConnections.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>

          {selectedNode ? (
            <>
              <div className="knowledge-viz-node-hero" data-node-type={selectedNode.type}>
                <span className="knowledge-viz-node-glyph" aria-hidden="true" />
                <div>
                  <strong>{selectedNode.label}</strong>
                  <span>
                    {graphLabel(selectedNode.graphKey)} · {selectedNode.type}
                    {selectedNode.nodeKind ? ` · ${selectedNode.nodeKind}` : ""}
                  </span>
                  {selectedNodeIsCentral ? (
                    <em className="knowledge-viz-central-badge">central</em>
                  ) : null}
                </div>
              </div>
              <div className="knowledge-viz-inspector-actions">
                <button type="button" onClick={() => centerNode(selectedNode.id)}>
                  Center
                </button>
                <button
                  type="button"
                  className={focusMode ? "active" : ""}
                  aria-pressed={focusMode}
                  onClick={() => {
                    setFocusMode((current) => !current);
                    cameraDirtyRef.current = false;
                  }}
                >
                  {focusMode ? "Show full graph" : "Focus 1 hop"}
                </button>
                <button
                  type="button"
                  className={selectedNodeIsCentral ? "active" : ""}
                  aria-pressed={selectedNodeIsCentral}
                  onClick={toggleCentralNode}
                >
                  {selectedNodeIsCentral ? "Central node" : "Mark central"}
                </button>
                {selectedNodeIsManuallyPlaced ? (
                  <button type="button" onClick={releaseSelectedNodePlacement}>
                    Release placement
                  </button>
                ) : null}
              </div>

              {selectedNode.summaryText ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Overview</h5>
                  <p>{selectedNode.summaryText}</p>
                </section>
              ) : null}

              {selectedConnections.length ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Relationships</h5>
                  <ol className="knowledge-viz-connection-list">
                    {selectedConnections.slice(0, connectionLimit).map((connection, index) => (
                      <li key={connection.id}>
                        <span className="knowledge-viz-connection-rank">{index + 1}</span>
                        <div className="knowledge-viz-connection-body">
                          <button
                            type="button"
                            className="knowledge-viz-connection-label"
                            onClick={() => selectInspectorNode(connection.nodeId)}
                          >
                            {connection.label}
                          </button>
                          <small className="knowledge-viz-connection-meta">
                            <button
                              type="button"
                              className="knowledge-viz-edge-link"
                              aria-label={`Inspect edge ${connection.predicate}`}
                              onClick={() => selectInspectorLink(connection.linkId)}
                            >
                              {connection.direction === "outgoing" ? "→" : "←"}{" "}
                              {connection.predicate}
                            </button>
                            {connection.relation ? <span>{connection.relation}</span> : null}
                            {connection.context ? <span>{connection.context}</span> : null}
                            <strong>{connection.score.toFixed(2)}</strong>
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                  {selectedConnections.length > connectionLimit ? (
                    <button
                      type="button"
                      className="knowledge-viz-show-connections"
                      onClick={() =>
                        setConnectionLimit((current) =>
                          Math.min(selectedConnections.length, current + 24),
                        )
                      }
                    >
                      Show {Math.min(24, selectedConnections.length - connectionLimit)} more of{" "}
                      {selectedConnections.length}
                    </button>
                  ) : null}
                </section>
              ) : null}

              {selectedNode.attributes && Object.keys(selectedNode.attributes).length ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Attributes</h5>
                  <StructuredValue value={selectedNode.attributes} />
                </section>
              ) : null}

              {Object.keys(selectedNodeAdditionalData).length ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Source data</h5>
                  <StructuredValue value={selectedNodeAdditionalData} />
                </section>
              ) : null}

              <section className="knowledge-viz-inspector-section">
                <h5>Record</h5>
                <dl>
                  <dt>graph</dt>
                  <dd>{graphLabel(selectedNode.graphKey)}</dd>
                  <dt>type</dt>
                  <dd>{selectedNode.type}</dd>
                  {selectedNode.nodeKind ? (
                    <>
                      <dt>kind</dt>
                      <dd>{selectedNode.nodeKind}</dd>
                    </>
                  ) : null}
                  <dt>level</dt>
                  <dd>{Number(selectedNode.level || 0) + 1}</dd>
                  <dt>weight</dt>
                  <dd>{Number(selectedNode.weight || 0)}</dd>
                  {selectedNode.importance != null ? (
                    <>
                      <dt>importance</dt>
                      <dd>{Number(selectedNode.importance || 0).toFixed(2)}</dd>
                    </>
                  ) : null}
                  {selectedNode.createdAt || selectedNode.created_at ? (
                    <>
                      <dt>created</dt>
                      <dd>{formatDate(selectedNode.createdAt || selectedNode.created_at)}</dd>
                    </>
                  ) : null}
                  {selectedNode.updatedAt || selectedNode.updated_at || selectedNode.latestDate ? (
                    <>
                      <dt>updated</dt>
                      <dd>
                        {formatDate(
                          selectedNode.updatedAt ||
                            selectedNode.updated_at ||
                            selectedNode.latestDate,
                        )}
                      </dd>
                    </>
                  ) : null}
                  {selectedNode.matchKey ? (
                    <>
                      <dt>bridge key</dt>
                      <dd>{selectedNode.matchKey}</dd>
                    </>
                  ) : null}
                </dl>
              </section>
            </>
          ) : selectedLink ? (
            <>
              <div className="knowledge-viz-edge-hero">
                <strong>{selectedLink.predicate || selectedLink.category || selectedLink.type}</strong>
                <span>
                  {selectedLinkSource?.label || getLinkEndpointId(selectedLink.source)} →{" "}
                  {selectedLinkTarget?.label || getLinkEndpointId(selectedLink.target)}
                </span>
              </div>
              <section className="knowledge-viz-inspector-section">
                <h5>Endpoints</h5>
                <div className="knowledge-viz-endpoint-list">
                  {selectedLinkSource ? (
                    <button
                      type="button"
                      onClick={() => selectInspectorNode(selectedLinkSource.id)}
                    >
                      <small>source</small>
                      <span>{selectedLinkSource.label}</span>
                    </button>
                  ) : null}
                  <span aria-hidden="true">→</span>
                  {selectedLinkTarget ? (
                    <button
                      type="button"
                      onClick={() => selectInspectorNode(selectedLinkTarget.id)}
                    >
                      <small>target</small>
                      <span>{selectedLinkTarget.label}</span>
                    </button>
                  ) : null}
                </div>
              </section>
              <section className="knowledge-viz-inspector-section">
                <h5>Evidence</h5>
                <dl>
                  <dt>relationship</dt>
                  <dd>{selectedLink.predicate || selectedLink.category || selectedLink.type}</dd>
                  <dt>kind</dt>
                  <dd>{selectedLink.type}</dd>
                  {selectedLink.epistemic_status ? (
                    <>
                      <dt>epistemic status</dt>
                      <dd>{selectedLink.epistemic_status}</dd>
                    </>
                  ) : null}
                  {selectedLink.confidence != null || selectedLink.weight != null ? (
                    <>
                      <dt>confidence</dt>
                      <dd>
                        {Number(selectedLink.confidence ?? selectedLink.weight ?? 0).toFixed(2)}
                      </dd>
                    </>
                  ) : null}
                  {selectedLink.metadata?.relationship ? (
                    <>
                      <dt>context</dt>
                      <dd>{selectedLink.metadata.relationship}</dd>
                    </>
                  ) : null}
                  {selectedLink.claim_id ? (
                    <>
                      <dt>claim id</dt>
                      <dd>{selectedLink.claim_id}</dd>
                    </>
                  ) : null}
                </dl>
              </section>
              {selectedLink.metadata && Object.keys(selectedLink.metadata).length ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Edge metadata</h5>
                  <StructuredValue value={selectedLink.metadata} />
                </section>
              ) : null}
              {Object.keys(selectedLinkAdditionalData).length ? (
                <section className="knowledge-viz-inspector-section">
                  <h5>Source data</h5>
                  <StructuredValue value={selectedLinkAdditionalData} />
                </section>
              ) : null}
            </>
          ) : (
            <div className="knowledge-viz-inspector-empty">
              <span aria-hidden="true">↗</span>
              <strong>Select a node or relationship</strong>
              <p>
                Click the graph to inspect connections, evidence, attributes, and update
                times. Double-click a node to center it.
              </p>
            </div>
          )}
        </aside>
      </div>

      <div className="knowledge-viz-legend" aria-label="Graph legend">
        {nodeTypes.map((nodeType) => (
          <span className="legend-chip node-type" key={nodeType}>
            <i style={{ background: colorByType.get(nodeType) || "#9ab0d9" }} />
            {nodeType.replaceAll("_", " ")}
          </span>
        ))}
        {activeLinkTypes.map((linkType) => (
          <span className={`legend-chip ${linkType}`} key={linkType}>
            {linkType === "cross" ? "cross-graph bridge" : `${linkType} relation`}
          </span>
        ))}
      </div>

      {activeGraphKeys.includes("knowledge") && knowledgeMetadata.available ? (
        <p className="knowledge-viz-data-note">
          Stored graph: {knowledgeGraph.nodes.length}
          {totalKnowledgeNodes > knowledgeGraph.nodes.length ? ` of ${totalKnowledgeNodes}` : ""}{" "}
          nodes, {knowledgeMetadata.claim_count || knowledgeGraph.claims.length}
          {totalKnowledgeClaims > Number(knowledgeMetadata.claim_count || 0)
            ? ` of ${totalKnowledgeClaims}`
            : ""}{" "}
          claims.
        </p>
      ) : null}
    </section>
  );
};

export default KnowledgeVisualizationsTab;
