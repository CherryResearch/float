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

export const buildCombinedGraphData = ({
  threadGraph,
  memoryGraph,
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
    // The knowledge-graph overlay is still a placeholder and contributes no nodes yet.
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
    graph.nodes.forEach((node) => {
      nodes.push({
        ...cloneNode(node),
        anchorX: slot.anchorX,
        anchorY: slot.anchorY,
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
    knowledge: 0,
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
  if (String(node?.type || "").endsWith("_anchor")) return 6.5;
  return 7;
};
