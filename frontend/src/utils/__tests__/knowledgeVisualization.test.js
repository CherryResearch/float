import {
  VISUALIZATION_HEIGHT,
  VISUALIZATION_WIDTH,
  applyManualPlacementForce,
  buildCombinedGraphData,
  buildThreadGraph,
  filterGraphData,
  getBaseNodeRadius,
  getNodeFocus,
  hasMeaningfulDrag,
  hydrateKnowledgeGraph,
  hydrateMemoryGraph,
  normalizeConversationName,
  rankNodeConnections,
  searchGraphNodes,
} from "../knowledgeVisualization";

describe("knowledgeVisualization helpers", () => {
  it("normalizes conversation names consistently", () => {
    expect(normalizeConversationName("folder/demo.json#msg-2")).toBe("folder/demo");
    expect(normalizeConversationName("")).toBe("(unknown)");
  });

  it("builds a namespaced thread graph with conversation match keys", () => {
    const graph = buildThreadGraph({
      thread_overview: {
        threads: [
          {
            label: "Design",
            item_count: 4,
            conversation_count: 1,
            conversation_breakdown: [
              {
                conversation: "project/session-a.json",
                item_count: 4,
                latest_date: "2026-03-07",
              },
            ],
          },
        ],
      },
    });

    expect(graph.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "threads:thread:Design",
          graphKey: "threads",
          level: 0,
        }),
        expect.objectContaining({
          id: "threads:conversation:project/session-a",
          matchKey: "conversation:project/session-a",
          level: 1,
        }),
      ]),
    );
    expect(graph.links).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "projection",
          source: "threads:thread:Design",
          target: "threads:conversation:project/session-a",
        }),
      ]),
    );
  });

  it("combines thread and memory graphs and emits cross-graph links for shared match keys", () => {
    const threadGraph = buildThreadGraph({
      thread_overview: {
        threads: [
          {
            label: "Design",
            item_count: 4,
            conversation_count: 1,
            conversation_breakdown: [
              {
                conversation: "project/session-a.json",
                item_count: 4,
              },
            ],
          },
        ],
      },
    });
    const memoryGraph = hydrateMemoryGraph({
      nodes: [
        {
          id: "memory:item:memory-one",
          label: "memory-one",
          type: "memory",
          level: 0,
          weight: 1,
        },
        {
          id: "memory:conversation:abc123",
          label: "project/session-a",
          type: "conversation_anchor",
          level: 1,
          weight: 2,
          match_key: "conversation:project/session-a",
        },
      ],
      links: [
        {
          source: "memory:item:memory-one",
          target: "memory:conversation:abc123",
          type: "explicit",
          category: "conversation",
          weight: 1,
        },
      ],
      metadata: {
        maxLevel: 1,
      },
    });

    expect(
      memoryGraph.nodes.find((node) => node.id === "memory:conversation:abc123")?.matchKey,
    ).toBe("conversation:project/session-a");

    const combined = buildCombinedGraphData({
      threadGraph,
      memoryGraph,
      includeThreadProjection: true,
      includeMemoryProjection: true,
      includeKnowledgeOverlay: false,
      levels: { threads: 0, memory: 1 },
      planeOffset: 0.35,
    });

    expect(combined.nodes).toHaveLength(4);
    expect(combined.crossLinks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "cross",
          matchKey: "conversation:project/session-a",
        }),
      ]),
    );
  });

  it("hydrates stored knowledge graph nodes with attributes and claim links", () => {
    const knowledgeGraph = hydrateKnowledgeGraph({
      nodes: [
        {
          id: "knowledge:person:alice",
          label: "Alice Nguyen",
          type: "person",
          node_kind: "entity",
          node_type: "person",
          attributes: { city: "Vancouver", role: "designer" },
        },
      ],
      links: [
        {
          source: "knowledge:person:alice",
          target: "knowledge:person:bob",
          type: "claim",
          predicate: "friend_of",
          confidence: 0.95,
        },
      ],
      metadata: { node_count: 1, claim_count: 1 },
    });

    expect(knowledgeGraph.nodes[0]).toEqual(
      expect.objectContaining({
        graphKey: "knowledge",
        nodeKind: "entity",
        nodeType: "person",
        attributes: { city: "Vancouver", role: "designer" },
      }),
    );
    expect(knowledgeGraph.links[0]).toEqual(
      expect.objectContaining({
        graphKey: "knowledge",
        weight: 0.95,
      }),
    );

    const combined = buildCombinedGraphData({
      threadGraph: { nodes: [], links: [], metadata: {} },
      memoryGraph: { nodes: [], links: [], metadata: {} },
      knowledgeGraph,
      includeThreadProjection: false,
      includeMemoryProjection: false,
      includeKnowledgeOverlay: true,
      levels: { knowledge: 0 },
      planeOffset: 0,
    });

    expect(combined.nodes).toHaveLength(1);
    expect(combined.links).toHaveLength(1);
    expect(combined.metadata.maxLevels.knowledge).toBe(0);
  });

  it("applies stored graph layout hints before falling back to radial placement", () => {
    const knowledgeGraph = hydrateKnowledgeGraph({
      nodes: [
        {
          id: "knowledge:person:self",
          label: "Self",
          type: "person",
          attributes: { layout_slot: "self" },
        },
        {
          id: "knowledge:person:friend-jules",
          label: "Jules Park",
          type: "person",
          attributes: { layout_slot: "club_friend" },
        },
        {
          id: "knowledge:org:computer-club",
          label: "Vancouver Computer Club",
          type: "organization",
          attributes: { layout_slot: "computer_club" },
        },
      ],
      links: [],
      metadata: { node_count: 3, claim_count: 0 },
    });

    const combined = buildCombinedGraphData({
      threadGraph: { nodes: [], links: [], metadata: {} },
      memoryGraph: { nodes: [], links: [], metadata: {} },
      knowledgeGraph,
      includeThreadProjection: false,
      includeMemoryProjection: false,
      includeKnowledgeOverlay: true,
      levels: { knowledge: 0 },
      planeOffset: 0,
    });
    const byLabel = new Map(combined.nodes.map((node) => [node.label, node]));

    expect(byLabel.get("Self")).toEqual(
      expect.objectContaining({
        anchorX: VISUALIZATION_WIDTH / 2,
        anchorY: VISUALIZATION_HEIGHT / 2,
      }),
    );
    expect(byLabel.get("Jules Park")?.anchorX).toBeGreaterThan(
      byLabel.get("Self")?.anchorX,
    );
    expect(byLabel.get("Vancouver Computer Club")?.anchorX).toBeGreaterThan(
      byLabel.get("Jules Park")?.anchorX,
    );
  });

  it("ranks selected-node connections using edge weights and relationship metadata", () => {
    const nodes = [
      { id: "knowledge:person:self", label: "Self", type: "person" },
      { id: "knowledge:person:friend-maya", label: "Maya Stone", type: "person" },
      { id: "knowledge:person:family-lena", label: "Lena Rivera", type: "person" },
      { id: "knowledge:org:job", label: "Float Systems Lab", type: "organization" },
    ];
    const links = [
      {
        source: "knowledge:person:self",
        target: "knowledge:org:job",
        predicate: "works_at",
        weight: 0.9,
        metadata: { relationship: "job" },
      },
      {
        source: "knowledge:person:self",
        target: "knowledge:person:friend-maya",
        predicate: "friend_of",
        weight: 0.95,
        metadata: { relationship: "friend", relationship_strength: "close" },
      },
      {
        source: "knowledge:person:self",
        target: "knowledge:person:family-lena",
        predicate: "family_of",
        weight: 0.96,
        metadata: { relationship: "family" },
      },
    ];

    const ranked = rankNodeConnections("knowledge:person:self", nodes, links);

    expect(ranked.map((connection) => connection.label)).toEqual([
      "Maya Stone",
      "Lena Rivera",
      "Float Systems Lab",
    ]);
    expect(ranked[0]).toEqual(
      expect.objectContaining({
        direction: "outgoing",
        linkId: expect.stringContaining("friend_of"),
        predicate: "friend_of",
        relation: "friend",
      }),
    );
  });

  it("keeps manually placed nodes near their dragged location without hard-pinning them", () => {
    const nodes = [
      { id: "alice", x: 20, y: 30, vx: 0, vy: 0 },
      { id: "bob", x: 80, y: 90, vx: 0, vy: 0 },
    ];
    applyManualPlacementForce(nodes, new Map([["alice", { x: 100, y: 70 }]]), 0.5, 0.75);

    expect(nodes[0]).toEqual(
      expect.objectContaining({
        vx: 30,
        vy: 15,
      }),
    );
    expect(nodes[1]).toEqual(expect.objectContaining({ vx: 0, vy: 0 }));
  });

  it("makes a central node visibly larger than the same ordinary node", () => {
    const person = { graphKey: "knowledge", type: "person" };

    expect(getBaseNodeRadius({ ...person, isCentral: true })).toBeGreaterThan(
      getBaseNodeRadius(person),
    );
  });

  it("distinguishes a click-sized pointer jitter from an intentional drag", () => {
    expect(hasMeaningfulDrag({ x: 100, y: 100 }, { x: 102, y: 101 })).toBe(false);
    expect(hasMeaningfulDrag({ x: 100, y: 100 }, { x: 108, y: 106 })).toBe(true);
  });

  it("fades and shrinks nodes that are further from the focused level", () => {
    const activeNode = { id: "one", graphKey: "threads", level: 0 };
    const fadedNode = { id: "two", graphKey: "threads", level: 2 };

    expect(getNodeFocus(activeNode, { threads: 0 }, "")).toEqual({
      opacity: 1,
      scale: 1.08,
    });
    expect(getNodeFocus(fadedNode, { threads: 0 }, "")).toEqual({
      opacity: 0.3,
      scale: 0.76,
    });
  });

  it("searches node labels, summaries, types, and nested attributes", () => {
    const nodes = [
      {
        id: "knowledge:person:alice",
        label: "Alice Nguyen",
        type: "person",
        summaryText: "Design lead",
        attributes: { location: { city: "Vancouver" } },
      },
      {
        id: "knowledge:org:float",
        label: "Float Systems",
        type: "organization",
        attributes: { discipline: "research" },
      },
    ];

    expect(searchGraphNodes(nodes, "alice").map((node) => node.id)).toEqual([
      "knowledge:person:alice",
    ]);
    expect(searchGraphNodes(nodes, "vancouver").map((node) => node.id)).toEqual([
      "knowledge:person:alice",
    ]);
    expect(searchGraphNodes(nodes, "organization").map((node) => node.id)).toEqual([
      "knowledge:org:float",
    ]);
  });

  it("filters node types, recency, links, and one-hop focus without dangling edges", () => {
    const now = Date.UTC(2026, 6, 29);
    const graph = {
      nodes: [
        {
          id: "alice",
          label: "Alice",
          type: "person",
          updatedAt: now / 1000,
        },
        {
          id: "bob",
          label: "Bob",
          type: "person",
          updatedAt: (now - 2 * 24 * 60 * 60 * 1000) / 1000,
        },
        {
          id: "archive",
          label: "Archive",
          type: "organization",
          updatedAt: (now - 60 * 24 * 60 * 60 * 1000) / 1000,
        },
      ],
      links: [
        { source: "alice", target: "bob", type: "claim" },
        { source: "bob", target: "archive", type: "claim" },
      ],
      crossLinks: [],
      metadata: {},
    };

    const recentPeople = filterGraphData(graph, {
      nodeTypes: ["person"],
      recentDays: 7,
      now,
    });
    expect(recentPeople.nodes.map((node) => node.id)).toEqual(["alice", "bob"]);
    expect(recentPeople.links).toHaveLength(1);

    const focused = filterGraphData(graph, {
      focusNodeId: "alice",
      focusMode: true,
      now,
    });
    expect(focused.nodes.map((node) => node.id)).toEqual(["alice", "bob"]);
    expect(focused.links).toHaveLength(1);
  });
});
