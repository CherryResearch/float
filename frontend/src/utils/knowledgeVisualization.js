export const VISUALIZATION_WIDTH = 980;
export const VISUALIZATION_HEIGHT = 560;

export const normalizeConversationName = (value) => {
  const raw = String(value || "").trim().replaceAll("\\", "/");
  if (!raw) return "(unknown)";
  const withoutAnchor = raw.split("#", 1)[0].trim();
  if (withoutAnchor.toLowerCase().endsWith(".json")) {
    return withoutAnchor.slice(0, -5) || "(unknown)";
  }
  return withoutAnchor || "(unknown)";
};

const normalizeThreadMatchKey = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  return `thread:${raw.replace(/\s+/g, " ")}`;
};

const cloneNode = (node) => ({ ...node });
const cloneLink = (link) => ({ ...link });

export const buildThreadGraph = (summary) => {
  const threads = Array.isArray(summary?.thread_overview?.threads)
    ? summary.thread_overview.threads
    : [];
  const threadNodes = [];
  const conversationNodes = new Map();
  const links = [];

  threads.slice(0, 24).forEach((thread, index) => {
    const label = String(thread?.label || "").trim() || `thread-${index + 1}`;
    const threadId = `threads:thread:${label}`;
    threadNodes.push({
      id: threadId,
      label,
      type: "thread",
      graphKey: "threads",
      level: 0,
      weight: Number(thread?.item_count || 0),
      conversationCount: Number(thread?.conversation_count || 0),
      matchKey: normalizeThreadMatchKey(label),
    });
    const breakdown = Array.isArray(thread?.conversation_breakdown)
      ? thread.conversation_breakdown
      : [];
    breakdown.slice(0, 12).forEach((row) => {
      const conversation = normalizeConversationName(row?.conversation);
      const convId = `threads:conversation:${conversation}`;
      if (!conversationNodes.has(convId)) {
        conversationNodes.set(convId, {
          id: convId,
          label: conversation,
          type: "conversation",
          graphKey: "threads",
          level: 1,
          weight: Number(row?.item_count || 0),
          latestDate: String(row?.latest_date || ""),
          matchKey: `conversation:${conversation}`,
        });
      }
      links.push({
        source: threadId,
        target: convId,
        weight: Number(row?.item_count || 0),
        type: "projection",
        graphKey: "threads",
      });
    });
  });

  return {
    graphKey: "threads",
    nodes: [...threadNodes, ...Array.from(conversationNodes.values())],
    links,
    metadata: {
      maxLevel: 1,
    },
  };
};

export const hydrateMemoryGraph = (graph) => {
  const rawNodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const rawLinks = Array.isArray(graph?.links) ? graph.links : [];
  const maxLevel = rawNodes.reduce((max, node) => {
    const level = Number(node?.level || 0);
    return Number.isFinite(level) ? Math.max(max, level) : max;
  }, 0);

  return {
    graphKey: "memory",
    nodes: rawNodes.map((node) => ({
      ...node,
      graphKey: "memory",
      level: Number(node?.level || 0),
      weight: Number(node?.weight || 0),
      matchKey: node?.matchKey || node?.match_key || "",
      refValue: node?.refValue || node?.ref_value || "",
      latestDate: node?.latestDate || node?.latest_date || "",
      conversationCount: Number(
        node?.conversationCount ?? node?.conversation_count ?? 0,
      ),
      itemCount: Number(node?.itemCount ?? node?.item_count ?? 0),
    })),
    links: rawLinks.map((link) => ({
      ...link,
      graphKey: "memory",
      weight: Number(link?.weight || 0),
    })),
    metadata: {
      ...(graph?.metadata || {}),
      maxLevel,
    },
  };
};

export const hydrateKnowledgeGraph = (graph) => {
  const rawNodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const rawLinks = Array.isArray(graph?.links) ? graph.links : [];
  const maxLevel = rawNodes.reduce((max, node) => {
    const level = Number(node?.level || 0);
    return Number.isFinite(level) ? Math.max(max, level) : max;
  }, 0);

  return {
    graphKey: "knowledge",
    nodes: rawNodes.map((node) => ({
      ...node,
      graphKey: "knowledge",
      level: Number(node?.level || 0),
      weight: Number(node?.weight || 0),
      matchKey: node?.matchKey || node?.match_key || "",
      nodeKind: node?.nodeKind || node?.node_kind || "",
      nodeType: node?.nodeType || node?.node_type || node?.type || "",
      summaryText: node?.summaryText || node?.summary_text || "",
      createdAt: node?.createdAt || node?.created_at || null,
      updatedAt: node?.updatedAt || node?.updated_at || null,
      attributes:
        node?.attributes && typeof node.attributes === "object" ? node.attributes : {},
    })),
    links: rawLinks.map((link) => ({
      ...link,
      graphKey: "knowledge",
      weight: Number(link?.weight || link?.confidence || 1),
    })),
    claims: Array.isArray(graph?.claims) ? graph.claims : [],
    metadata: {
      ...(graph?.metadata || {}),
      maxLevel,
    },
  };
};

const graphSlotsFor = (activeGraphs, planeOffset) => {
  if (!activeGraphs.length) return new Map();
  const slots = new Map();
  const count = activeGraphs.length;
  const offset = Number(planeOffset || 0);
  activeGraphs.forEach((graph, index) => {
    const depth = index - (count - 1) / 2;
    const baseX =
      count === 1
        ? VISUALIZATION_WIDTH / 2
        : VISUALIZATION_WIDTH * (0.2 + (index * 0.6) / Math.max(count - 1, 1));
    slots.set(graph.graphKey, {
      anchorX: baseX + depth * offset * 140,
      anchorY: VISUALIZATION_HEIGHT / 2 + depth * offset * 28,
      depth,
    });
  });
  return slots;
};

const knowledgeLayoutSlots = {
  self: [0, 0],
  close_friend: [-260, -95],
  club_friend: [225, -105],
  computer_club: [360, -170],
  coworker: [205, 155],
  job: [360, 105],
  family: [-255, 150],
};

const knowledgeNodeAnchor = (node, index, graph, slot) => {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const layoutSlot = String(
    node?.attributes?.layout_slot || node?.layout_slot || node?.metadata?.layout_slot || "",
  );
  const layoutOffset = knowledgeLayoutSlots[layoutSlot];
  if (layoutOffset) {
    return {
      anchorX: slot.anchorX + layoutOffset[0],
      anchorY: slot.anchorY + layoutOffset[1],
    };
  }

  const nodeId = String(node?.id || node?.node_id || "");
  const label = String(node?.label || node?.canonical_name || "");
  if (!nodes.length || /(^|:)self$/i.test(nodeId) || label.toLowerCase() === "self") {
    return {
      anchorX: slot.anchorX,
      anchorY: slot.anchorY,
    };
  }

  const otherNodes = nodes.filter((candidate) => {
    const candidateId = String(candidate?.id || candidate?.node_id || "");
    const candidateLabel = String(candidate?.label || candidate?.canonical_name || "");
    return !/(^|:)self$/i.test(candidateId) && candidateLabel.toLowerCase() !== "self";
  });
  const ringIndex = Math.max(0, otherNodes.findIndex((candidate) => candidate === node));
  const ringCount = Math.max(1, otherNodes.length);
  const angle = -Math.PI / 2 + (2 * Math.PI * ringIndex) / ringCount;
  const radius = ringCount <= 8 ? 310 : 235;
  const type = String(node?.type || node?.nodeType || node?.node_type || "").toLowerCase();
  const typeRadiusBoost = type === "organization" ? 34 : 0;

  return {
    anchorX: slot.anchorX + Math.cos(angle) * (radius + typeRadiusBoost),
    anchorY: slot.anchorY + Math.sin(angle) * (radius * 0.68 + typeRadiusBoost * 0.4),
  };
};

export const buildCombinedGraphData = ({
  threadGraph,
  memoryGraph,
  knowledgeGraph,
  includeThreadProjection,
  includeMemoryProjection,
  includeKnowledgeOverlay,
  planeOffset,
}) => {
  const selectedGraphs = [];
  if (includeThreadProjection && threadGraph?.nodes?.length) {
    selectedGraphs.push(threadGraph);
  }
  if (includeMemoryProjection && memoryGraph?.nodes?.length) {
    selectedGraphs.push(memoryGraph);
  }
  if (includeKnowledgeOverlay) {
    if (knowledgeGraph?.nodes?.length) {
      selectedGraphs.push(knowledgeGraph);
    }
  }

  const slots = graphSlotsFor(selectedGraphs, planeOffset);
  const nodes = [];
  const links = [];

  selectedGraphs.forEach((graph) => {
    const slot = slots.get(graph.graphKey) || {
      anchorX: VISUALIZATION_WIDTH / 2,
      anchorY: VISUALIZATION_HEIGHT / 2,
      depth: 0,
    };
    graph.nodes.forEach((node, index) => {
      const nodeAnchor =
        graph.graphKey === "knowledge"
          ? knowledgeNodeAnchor(node, index, graph, slot)
          : {
              anchorX: slot.anchorX,
              anchorY: slot.anchorY,
            };
      nodes.push({
        ...cloneNode(node),
        anchorX: nodeAnchor.anchorX,
        anchorY: nodeAnchor.anchorY,
        depth: slot.depth,
      });
    });
    graph.links.forEach((link) => {
      links.push(cloneLink(link));
    });
  });

  const nodesByMatchKey = new Map();
  nodes.forEach((node) => {
    const matchKey = String(node?.matchKey || "").trim();
    if (!matchKey) return;
    const bucket = nodesByMatchKey.get(matchKey) || [];
    bucket.push(node);
    nodesByMatchKey.set(matchKey, bucket);
  });

  const crossLinks = [];
  nodesByMatchKey.forEach((bucket, matchKey) => {
    if (!Array.isArray(bucket) || bucket.length < 2) return;
    for (let index = 0; index < bucket.length; index += 1) {
      const left = bucket[index];
      for (let next = index + 1; next < bucket.length; next += 1) {
        const right = bucket[next];
        if (left.graphKey === right.graphKey) continue;
        crossLinks.push({
          source: left.id,
          target: right.id,
          type: "cross",
          graphKey: "cross",
          matchKey,
          weight: 1,
        });
      }
    }
  });

  const maxLevels = {
    threads: Number(threadGraph?.metadata?.maxLevel || 0),
    memory: Number(memoryGraph?.metadata?.maxLevel || 0),
    knowledge: Number(knowledgeGraph?.metadata?.maxLevel || 0),
  };

  return {
    nodes,
    links,
    crossLinks,
    metadata: {
      activeGraphCount: selectedGraphs.length,
      maxLevels,
    },
  };
};

export const getNodeFocus = (node, levels, selectedNodeId) => {
  const focusLevel = Number(levels?.[node?.graphKey] || 0);
  const nodeLevel = Number(node?.level || 0);
  const distance = Math.abs(nodeLevel - focusLevel);
  const opacityScale = [1, 0.58, 0.3, 0.16][Math.min(distance, 3)];
  const sizeScale = [1.08, 0.92, 0.76, 0.66][Math.min(distance, 3)];
  if (node?.id === selectedNodeId) {
    return { opacity: 1, scale: 1.18 };
  }
  return { opacity: opacityScale, scale: sizeScale };
};

export const getBaseNodeRadius = (node) => {
  let radius = 7;
  if (node?.type === "thread") radius = 11;
  else if (node?.type === "memory") radius = 8.5;
  else if (node?.graphKey === "knowledge" && node?.type === "person") radius = 12;
  else if (node?.graphKey === "knowledge") radius = 10;
  else if (String(node?.type || "").endsWith("_anchor")) radius = 6.5;
  return node?.isCentral ? radius * 1.38 : radius;
};

export const applyManualPlacementForce = (
  nodes = [],
  manualPositions = new Map(),
  alpha = 1,
  strength = 0.72,
) => {
  nodes.forEach((node) => {
    const target = manualPositions.get(node?.id);
    if (!target || !Number.isFinite(node?.x) || !Number.isFinite(node?.y)) return;
    node.vx = Number(node.vx || 0) + (target.x - node.x) * strength * alpha;
    node.vy = Number(node.vy || 0) + (target.y - node.y) * strength * alpha;
  });
};

export const hasMeaningfulDrag = (start, end, threshold = 4) => {
  if (
    !Number.isFinite(start?.x) ||
    !Number.isFinite(start?.y) ||
    !Number.isFinite(end?.x) ||
    !Number.isFinite(end?.y)
  ) {
    return false;
  }
  return Math.hypot(end.x - start.x, end.y - start.y) >= threshold;
};

const linkEndpointId = (endpoint) => {
  if (typeof endpoint === "string") return endpoint;
  if (endpoint && typeof endpoint === "object") return String(endpoint.id || "");
  return "";
};

export const getLinkEndpointId = linkEndpointId;

export const getGraphLinkId = (link, index = 0) => {
  const sourceId = linkEndpointId(link?.source);
  const targetId = linkEndpointId(link?.target);
  const relationId =
    link?.claim_id || link?.predicate || link?.category || link?.type || "connected";
  return `${sourceId}:${targetId}:${relationId}:${index}`;
};

const searchableValue = (value) => {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(searchableValue).join(" ");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, nestedValue]) => `${key} ${searchableValue(nestedValue)}`)
      .join(" ");
  }
  return String(value);
};

export const searchGraphNodes = (nodes = [], query = "", limit = 8) => {
  const normalizedQuery = String(query || "")
    .trim()
    .toLocaleLowerCase();
  if (!normalizedQuery) return [];

  return nodes
    .map((node) => {
      const label = String(node?.label || "");
      const type = String(node?.type || node?.nodeType || "");
      const haystack = [
        label,
        type,
        node?.nodeKind,
        node?.summaryText,
        searchableValue(node?.attributes),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase();
      const labelIndex = label.toLocaleLowerCase().indexOf(normalizedQuery);
      const matchIndex = haystack.indexOf(normalizedQuery);
      if (matchIndex < 0) return null;
      return {
        node,
        score:
          labelIndex === 0 ? 3 : labelIndex > 0 ? 2 : type.toLowerCase() === normalizedQuery ? 2 : 1,
      };
    })
    .filter(Boolean)
    .sort(
      (left, right) =>
        right.score - left.score ||
        String(left.node?.label || "").localeCompare(String(right.node?.label || "")),
    )
    .slice(0, Math.max(1, Number(limit || 8)))
    .map(({ node }) => node);
};

const nodeTimestamp = (node) => {
  const raw = node?.updatedAt || node?.updated_at || node?.latestDate || node?.latest_date;
  if (raw == null || raw === "") return null;
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) {
    return numeric > 10_000_000_000 ? numeric : numeric * 1000;
  }
  const parsed = Date.parse(String(raw));
  return Number.isFinite(parsed) ? parsed : null;
};

export const filterGraphData = (
  graphData,
  {
    nodeTypes = [],
    excludedNodeTypes = [],
    recentDays = 0,
    focusNodeId = "",
    focusMode = false,
    now = Date.now(),
  } = {},
) => {
  const nodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
  const links = [
    ...(Array.isArray(graphData?.links) ? graphData.links : []),
    ...(Array.isArray(graphData?.crossLinks) ? graphData.crossLinks : []),
  ];
  const selectedTypes = new Set(nodeTypes.filter(Boolean));
  const excludedTypes = new Set(excludedNodeTypes.filter(Boolean));
  const cutoff = recentDays > 0 ? now - recentDays * 24 * 60 * 60 * 1000 : null;
  const focusIds = new Set();

  if (focusMode && focusNodeId) {
    focusIds.add(String(focusNodeId));
    links.forEach((link) => {
      const sourceId = linkEndpointId(link?.source);
      const targetId = linkEndpointId(link?.target);
      if (sourceId === focusNodeId) focusIds.add(targetId);
      if (targetId === focusNodeId) focusIds.add(sourceId);
    });
  }

  const filteredNodes = nodes.filter((node) => {
    if (selectedTypes.size && !selectedTypes.has(String(node?.type || "unknown"))) {
      return false;
    }
    if (excludedTypes.has(String(node?.type || "unknown"))) return false;
    if (cutoff != null) {
      const timestamp = nodeTimestamp(node);
      if (timestamp == null || timestamp < cutoff) return false;
    }
    if (focusIds.size && !focusIds.has(String(node?.id || ""))) return false;
    return true;
  });
  const visibleNodeIds = new Set(filteredNodes.map((node) => String(node?.id || "")));
  const visibleLink = (link) =>
    visibleNodeIds.has(linkEndpointId(link?.source)) &&
    visibleNodeIds.has(linkEndpointId(link?.target));

  return {
    ...graphData,
    nodes: filteredNodes,
    links: (graphData?.links || []).filter(visibleLink),
    crossLinks: (graphData?.crossLinks || []).filter(visibleLink),
    metadata: {
      ...(graphData?.metadata || {}),
      unfilteredNodeCount: nodes.length,
      filteredNodeCount: filteredNodes.length,
    },
  };
};

const relationshipBoost = (link) => {
  const metadata = link?.metadata && typeof link.metadata === "object" ? link.metadata : {};
  const strength = String(metadata.relationship_strength || "").toLowerCase();
  if (strength === "close") return 0.18;
  if (strength === "family") return 0.12;
  if (strength === "co-worker") return 0.08;
  return 0;
};

export const rankNodeConnections = (selectedNodeId, nodes = [], links = []) => {
  const selectedId = String(selectedNodeId || "");
  if (!selectedId) return [];
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  return links
    .map((link, index) => {
      const sourceId = linkEndpointId(link?.source);
      const targetId = linkEndpointId(link?.target);
      if (sourceId !== selectedId && targetId !== selectedId) return null;
      const otherId = sourceId === selectedId ? targetId : sourceId;
      const otherNode = nodeById.get(otherId);
      if (!otherNode) return null;
      const weight = Number(link?.weight ?? link?.confidence ?? 0);
      const score = weight + relationshipBoost(link);
      const linkId = link?.__graphLinkId || getGraphLinkId(link, index);
      return {
        id: `${selectedId}:${linkId}`,
        linkId,
        nodeId: otherId,
        label: otherNode.label || otherId,
        type: otherNode.type || "",
        direction: sourceId === selectedId ? "outgoing" : "incoming",
        sourceId,
        targetId,
        predicate: link?.predicate || link?.category || link?.type || "connected",
        relation: link?.metadata?.relationship || "",
        context: link?.metadata?.context || "",
        metadata:
          link?.metadata && typeof link.metadata === "object" ? link.metadata : {},
        score,
        weight,
      };
    })
    .filter(Boolean)
    .sort((left, right) => right.score - left.score || left.label.localeCompare(right.label));
};
