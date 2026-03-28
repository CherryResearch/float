export const serializeMemoryValue = (row) => {
  if (!row) return "";
  if (row.sensitivity === "secret" && (row.encrypted || row.decrypt_error)) {
    return String(row.hint || "");
  }
  try {
    if (typeof row.value === "string") return row.value;
    return JSON.stringify(row.value ?? "");
  } catch {
    return String(row.value ?? "");
  }
};

export const buildMemorySearchText = (row) =>
  [row?.key, row?.hint, serializeMemoryValue(row)]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

export const getMemoryFilterTimestamp = (row, field) => {
  if (!row) return null;
  const raw = row?.[field];
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

export const normalizeDateBoundary = (value, boundary) => {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const suffix = boundary === "end" ? "T23:59:59.999" : "T00:00:00.000";
  const parsed = new Date(`${raw}${suffix}`);
  if (Number.isNaN(parsed.getTime())) return null;
  return Math.floor(parsed.getTime() / 1000);
};

export const buildGraphContext = (graph, key) => {
  if (!graph || !key) return null;
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const links = Array.isArray(graph?.links) ? graph.links : [];
  const nodeId = `memory:item:${key}`;
  const selectedNode = nodes.find((node) => node?.id === nodeId) || null;
  if (!selectedNode) {
    return {
      selectedNode: null,
      anchors: [],
      threads: [],
      neighbors: [],
      metadata: graph?.metadata || {},
    };
  }
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const directLinks = links.filter(
    (link) => link?.source === nodeId || link?.target === nodeId,
  );
  const anchors = directLinks
    .filter((link) => link?.type === "explicit")
    .map((link) => {
      const otherId = link.source === nodeId ? link.target : link.source;
      const anchorNode = nodeById.get(otherId);
      if (!anchorNode) return null;
      return {
        id: otherId,
        label: anchorNode.label || otherId,
        category: anchorNode.category || "anchor",
        refValue: anchorNode.ref_value || anchorNode.label || otherId,
        weight: Number(anchorNode.weight || link.weight || 0),
      };
    })
    .filter(Boolean)
    .sort((left, right) => {
      if (left.category !== right.category) {
        return String(left.category).localeCompare(String(right.category));
      }
      return String(left.label).localeCompare(String(right.label));
    });
  const anchorIds = new Set(anchors.map((anchor) => anchor.id));
  const threadsById = new Map();
  links
    .filter((link) => link?.type === "projection")
    .forEach((link) => {
      const source = link?.source;
      const target = link?.target;
      const anchorId = anchorIds.has(source) ? source : anchorIds.has(target) ? target : "";
      if (!anchorId) return;
      const otherId = anchorId === source ? target : source;
      const threadNode = nodeById.get(otherId);
      if (!threadNode || threadNode.type !== "thread") return;
      const anchorNode = nodeById.get(anchorId) || {};
      const existing = threadsById.get(otherId);
      const viaConversation = String(anchorNode.label || anchorNode.ref_value || "").trim();
      if (existing) {
        if (viaConversation && !existing.viaConversations.includes(viaConversation)) {
          existing.viaConversations.push(viaConversation);
        }
        existing.weight = Math.max(existing.weight, Number(link.weight || 0));
        if (!existing.latestDate && threadNode.latest_date) {
          existing.latestDate = threadNode.latest_date;
        }
        return;
      }
      threadsById.set(otherId, {
        id: otherId,
        label: threadNode.label || otherId,
        weight: Number(link.weight || threadNode.weight || 0),
        conversationCount: Number(
          threadNode.conversationCount ?? threadNode.conversation_count ?? 0,
        ),
        itemCount: Number(threadNode.itemCount ?? threadNode.item_count ?? 0),
        latestDate: threadNode.latestDate || threadNode.latest_date || "",
        viaConversations: viaConversation ? [viaConversation] : [],
      });
    });
  const threads = Array.from(threadsById.values()).sort((left, right) => {
    if (right.weight !== left.weight) return right.weight - left.weight;
    return String(left.label).localeCompare(String(right.label));
  });
  const neighbors = directLinks
    .filter((link) => link?.type === "semantic")
    .map((link) => {
      const otherId = link.source === nodeId ? link.target : link.source;
      const neighborNode = nodeById.get(otherId);
      if (!neighborNode) return null;
      return {
        id: otherId,
        label: neighborNode.label || otherId,
        weight: Number(link.weight || 0),
        sharedExplicitCount: Number(link.shared_explicit_count || 0),
        tokenOverlap: Number(link.token_overlap || 0),
        importance: Number(neighborNode.importance || neighborNode.weight || 0),
        sensitivity: neighborNode.sensitivity || "mundane",
        memorized: !!neighborNode.memorized,
      };
    })
    .filter(Boolean)
    .sort((left, right) => right.weight - left.weight);

  return {
    selectedNode,
    anchors,
    threads,
    neighbors,
    metadata: graph?.metadata || {},
  };
};
