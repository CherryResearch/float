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
  levels,
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
        focusLevel: Number(levels?.[graph.graphKey] || 0),
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
  if (node?.type === "thread") return 11;
  if (node?.type === "memory") return 8.5;
  if (node?.graphKey === "knowledge" && node?.type === "person") return 12;
  if (node?.graphKey === "knowledge") return 10;
  if (String(node?.type || "").endsWith("_anchor")) return 6.5;
  return 7;
};

const linkEndpointId = (endpoint) => {
  if (typeof endpoint === "string") return endpoint;
  if (endpoint && typeof endpoint === "object") return String(endpoint.id || "");
  return "";
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
    .map((link) => {
      const sourceId = linkEndpointId(link?.source);
      const targetId = linkEndpointId(link?.target);
      if (sourceId !== selectedId && targetId !== selectedId) return null;
      const otherId = sourceId === selectedId ? targetId : sourceId;
      const otherNode = nodeById.get(otherId);
      if (!otherNode) return null;
      const weight = Number(link?.weight ?? link?.confidence ?? 0);
      const score = weight + relationshipBoost(link);
      return {
        id: `${selectedId}:${otherId}:${link?.claim_id || link?.predicate || link?.type}`,
        nodeId: otherId,
        label: otherNode.label || otherId,
        type: otherNode.type || "",
        predicate: link?.predicate || link?.category || link?.type || "connected",
        relation: link?.metadata?.relationship || "",
        context: link?.metadata?.context || "",
        score,
        weight,
      };
    })
    .filter(Boolean)
    .sort((left, right) => right.score - left.score || left.label.localeCompare(right.label));
};
